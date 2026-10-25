"""上传入库流水线（DEVELOPMENT.md 5.10）。

parse_file → split_chunks → (parent chunks) embed_documents(batch) → vector_store.add_chunks
- 后台单线程 Worker（ThreadPoolExecutor max_workers=2），不占 HTTP worker 线程。
- 入库只存父块（5.10 明确 parent chunks）；子块文本供 W3 上下文回溯，本阶段不落库。
- 幂等：content_hash 命中（Chroma 持久 + 内存双检）返回既有 document_id，不重复入库。
- 失败：Job 标记 failed + error_code（ParseError→30003 / 向量化→30002 /
  解析质量门禁 QualityGateError→30005 / 注入→40002 / 审核→40003 / 其他→50001）。
"""
from __future__ import annotations

import hashlib
import io
import threading
import time
import queue
from pathlib import Path
from uuid import uuid4

from app.core.config import SystemConfig
from app.core.domain import Job
from app.core.errors import (
    ContentModerationError,
    EmbeddingError,
    EmptyDocumentError,
    InjectionError,
    ParseError,
    QualityGateError,
)
from app.core.index_dna import compute_index_dna
from app.core.interfaces import VectorEngine, VectorStore
from app.core import metrics
from app.core.logging import get_logger, set_trace_id
from app.core.persist import atomic_write_bytes
from app.services.documents_store import DocumentsStore
from app.services.jobs_store import JobStore
from app.adapters.chunker import split_chunks
from app.adapters.parser import evaluate_parse_quality, parse_file, reindex_tables

logger = get_logger("ingestion")


def compute_content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class IngestionPipeline:
    def __init__(self, config: SystemConfig, embedding: VectorEngine,
                 vector_store: VectorStore, jobs: JobStore,
                 documents: DocumentsStore, kbs=None, max_workers: int = 2,
                 start_workers: bool = True):
        self.config = config
        self.embedding = embedding
        self.vector_store = vector_store
        self.jobs = jobs
        self.documents = documents
        self.kbs = kbs                   # P2-6：库级 pii 策略解析（None=不启用库级覆盖）
        # P1-3：索引 DNA——入库时写入 chunk.metadata 与 DocEntry（装配期计算一次，
        # /api/plugins/switch 热切换重建 deps 时随新 config 重算）
        self._index_dna = compute_index_dna(config)["dna"]
        # C5（V5.1）：有界队列 + 固定 worker（替代无界 ThreadPoolExecutor），
        # 队列满时 submit 拒绝并标记 Job failed（43003），防止大批量上传内存无限堆积
        self._queue: queue.Queue = queue.Queue(maxsize=config.ingestion.max_queue_size)
        self._workers = [threading.Thread(target=self._worker_loop, daemon=True,
                                          name=f"ingest-{i}") for i in range(max_workers)]
        if start_workers:
            for w in self._workers:
                w.start()
        self._hash_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._closing = False                # C4：shutdown 置位后拒绝新提交，防 job 永久 pending

    def _worker_loop(self) -> None:
        while True:
            item = self._queue.get()
            if item[0] is None:        # C4：shutdown 哨兵（首元素 None）
                self._queue.task_done()
                break
            raw, filename, job, kb_id, collection, replace_document_id, force = item
            metrics.set_gauge("rag_ingest_queue_depth", self._queue.qsize(),
                              help_text="入库队列当前深度（P2-7）")
            try:
                self._run(raw, filename, job, kb_id=kb_id, collection=collection,
                          replace_document_id=replace_document_id, force=force)
            except Exception:
                self.jobs.update(job.job_id, status="failed", error_code="50001")
            finally:
                self._queue.task_done()

    def shutdown(self, timeout: float = 10.0) -> None:
        """C4：停 worker（热切换/退出时释放线程）。

        先置关闭标志（新提交被拒绝，标记 50003），再 drain 队列中未处理任务
        （标记中断 50003，复用 C6 中断语义），随后投递哨兵让各 worker 退出；
        join 超时不强杀（daemon 线程，进程退出不阻塞）。
        """
        from app.services.jobs_store import INTERRUPTED_CODE

        self._closing = True
        while True:
            try:
                raw, filename, job, kb_id, collection, _replace_id, _force = self._queue.get_nowait()
                self.jobs.update(job.job_id, status="failed", error_code=INTERRUPTED_CODE)
                self._queue.task_done()
            except queue.Empty:
                break
        for _ in self._workers:
            self._queue.put((None, None, None, None, None, None, None))
        for worker in self._workers:
            worker.join(timeout=timeout)

    def _lock_for(self, content_hash: str) -> threading.Lock:
        """按 content_hash 取互斥锁（并发同内容上传串行化，防 TOCTOU 双份入库）。"""
        with self._locks_guard:
            lock = self._hash_locks.get(content_hash)
            if lock is None:
                lock = threading.Lock()
                self._hash_locks[content_hash] = lock
            return lock

    # -- 幂等查询 --------------------------------------------------------------

    def find_document_id_by_hash(self, content_hash: str,
                                 collection: str | None = None,
                                 kb_id: str = "default") -> str | None:
        """Chroma 持久事实优先，内存文档表兜底（重启后仍能去重）；按库作用域（13.5）。"""
        collection = collection or self.config.vector_store.collection
        getter = getattr(self.vector_store, "get_document_id_by_hash", None)
        if getter is not None:
            doc_id = getter(content_hash, collection=collection)
            if doc_id:
                return doc_id
        return self.documents.by_hash(content_hash, kb_id=kb_id)

    # -- 提交/执行 -------------------------------------------------------------

    def submit(self, raw: bytes, filename: str, job: Job,
               kb_id: str = "default", collection: str | None = None,
               replace_document_id: str | None = None, force: bool = False) -> bool:
        """异步提交入库任务（HTTP 线程立即返回）。

        C5：队列满时拒绝提交，Job 标记 failed（error_code=43003），返回 False。
        C4：pipeline 关闭中（热切换/退出）拒绝新提交并标记 50003，防止 job 永久 pending。
        V5.2：replace_document_id——增量更新时被替换的旧版本（成功入库后删除旧块）；
              force——同内容强制重新入库（修复 0 块/配置变更场景）。
        """
        if self._closing:
            from app.services.jobs_store import INTERRUPTED_CODE

            self.jobs.update(job.job_id, status="failed", error_code=INTERRUPTED_CODE)
            return False
        try:
            self._queue.put_nowait((raw, filename, job, kb_id, collection,
                                    replace_document_id, force))
            return True
        except queue.Full:
            self.jobs.update(job.job_id, status="failed", error_code="43003")
            return False

    def ingest(self, raw: bytes, filename: str, job: Job | None = None,
               kb_id: str = "default", collection: str | None = None,
               replace_document_id: str | None = None, force: bool = False) -> Job:
        """同步入库（demo-ingest / 单测用），返回更新后的 Job。

        kb_id/collection：13.5 多知识库作用域；collection 缺省按 kb 解析（default → "kb"）。
        V5.2：replace_document_id / force 语义同 submit。
        """
        job = job or self.jobs.create(filename, kb_id=kb_id)
        return self._run(raw, filename, job, kb_id=kb_id, collection=collection,
                         replace_document_id=replace_document_id, force=force)

    def _resolve_pii_policy(self, kb_id: str) -> dict | None:
        """P2-6：解析生效的 PII 策略（kb 库级 > 全局 > 旧开关兼容）；None=不处理。

        库级非法配置降级为"不处理"并 warning（不误伤入库主流程）。
        """
        pii = self.config.ingestion.pii
        kb_pii = None
        if self.kbs is not None:
            info = self.kbs.get(kb_id)
            kb_pii = getattr(info, "pii", None) if info is not None else None
        if kb_pii is not None:
            return self._normalize_pii(kb_pii, source=f"kb:{kb_id}")
        if pii.enabled:
            if self.config.ingestion.desensitize_enabled:
                logger.warning(
                    "旧开关 desensitize_enabled 与 ingestion.pii 同时开启：按新逻辑（pii）执行，"
                    "旧开关已 deprecated（两个周期后移除）",
                    extra={"extra_fields": {"kb_id": kb_id}},
                )
            return {"mode": pii.mode, "types": list(pii.types)}
        if self.config.ingestion.desensitize_enabled:
            # 仅旧开关开启（兼容期）：等价 mask + 全类型
            return {"mode": "mask", "types": ["id_card", "phone", "bank_card"]}
        return None

    @staticmethod
    def _normalize_pii(raw: dict, source: str) -> dict | None:
        """校验库级 pii 配置形状；enabled=false 显式关闭返回 None，非法项降级 None。"""
        if not isinstance(raw, dict):
            logger.warning("库级 pii 配置非法（非 dict），忽略", extra={"extra_fields": {"source": source}})
            return None
        if raw.get("enabled") is False:
            return None
        mode = raw.get("mode", "mask")
        types = list(raw.get("types") or ["id_card", "phone", "bank_card"])
        from app.services.desensitize import VALID_MODES, VALID_TYPES

        if mode not in VALID_MODES or any(t not in VALID_TYPES for t in types):
            logger.warning(
                "库级 pii 配置非法（mode/types），降级为不处理",
                extra={"extra_fields": {"source": source, "mode": mode, "types": types}},
            )
            return None
        return {"mode": mode, "types": types}

    def _archive_original(self, raw: bytes, filename: str, kb_id: str, document_id: str) -> str | None:
        """P0-2：原始字节归档（尽力而为），目标 `{archive.dir}/{kb_id}/{document_id}{ext}`。

        - ext 只取 `Path(filename).suffix`：恶意文件名（如 "../../x"）的 suffix 为空串，
          路径各段均来自受约束字段（kb_id 正则约束 / document_id 为 uuid hex），无路径穿越面；
        - 写失败 warning 后返回 None（archive_path 置空 = 未归档），不影响入库主流程。
        """
        cfg = self.config.ingestion.archive
        if not cfg.enabled:
            return None
        suffix = Path(filename).suffix
        target = Path(cfg.dir) / kb_id / f"{document_id}{suffix}"
        try:
            atomic_write_bytes(target, raw)
            return str(target)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "原件归档失败（继续入库，archive_path 置空）",
                extra={"extra_fields": {"document_id": document_id, "archive_dir": cfg.dir,
                                        "error": str(exc)}},
            )
            return None

    def _run(self, raw: bytes, filename: str, job: Job,
             kb_id: str = "default", collection: str | None = None,
             replace_document_id: str | None = None, force: bool = False) -> Job:
        # worker 线程无请求上下文，生成独立 trace_id 贯穿本 Job 日志
        set_trace_id(uuid4().hex[:12])
        collection = collection or self.config.vector_store.collection
        self.jobs.update(job.job_id, status="running")
        stage_timings: dict[str, int] = {}   # P2-7：阶段耗时 ms（parse/clean/chunk/embed/index）
        t0 = time.perf_counter()

        def _stage(name: str) -> None:
            """记录上一阶段耗时（ms）并写入 histogram（秒）。"""
            stage_timings[name] = round((time.perf_counter() - t0) * 1000)
            metrics.observe("rag_ingest_stage_seconds", stage_timings[name] / 1000,
                            {"stage": name}, help_text="入库各阶段耗时分布（P2-7）")
        try:
            doc = parse_file(io.BytesIO(raw), filename, config=self.config.ingestion)
            doc.metadata["kb_id"] = kb_id          # 入库时写 Chunk.metadata.kb_id（13.5）
            doc.metadata["index_dna"] = self._index_dna   # P1-3：dna 随 chunker 继承进 chunk metadata

            # P0-1：解析质量门禁（parse 之后、脱敏之前；默认关闭=行为与现状完全一致）。
            # reject 模式在此抛 QualityGateError，由本函数尾部错误映射统一置 Job failed
            # （worker 线程与同步 ingest 同路径），不在此处自行吞掉异常。
            gate = self.config.ingestion.quality_gate
            if gate.enabled:
                flags = evaluate_parse_quality(
                    doc.text, doc.metadata.get("text_stats") or {},
                    min_text_chars=gate.min_text_chars,
                    max_garbled_ratio=gate.max_garbled_ratio,
                    min_pages_with_text_ratio=gate.min_pages_with_text_ratio,
                )
                if flags:
                    if gate.mode == "reject":
                        raise QualityGateError(data={"quality_flags": flags})
                    # flag 模式：打标（list[str]，Chroma metadata 可存储）+ warning 后继续入库
                    doc.metadata["quality_flags"] = flags
                    logger.warning(
                        "解析质量门禁打标（flag 模式，继续入库）",
                        extra={"extra_fields": {"job_id": job.job_id, "filename": filename,
                                                "quality_flags": flags}},
                    )

            _stage("parse")   # P2-7：parse 段含解析与质量门禁

            # S5（13.11.1）/ P2-6：PII 两级化（kb 库级策略 > 全局 ingestion.pii > 旧开关兼容 > 不处理）
            pii_policy = self._resolve_pii_policy(kb_id)
            if pii_policy:
                from app.services.desensitize import detect_pii, desensitize_text

                hits = detect_pii(doc.text)
                if hits:
                    doc.metadata["pii_types"] = sorted({h["type"] for h in hits})   # 仅打标类型集合
                    doc.text = desensitize_text(doc.text, mode=pii_policy["mode"],
                                                types=pii_policy["types"])

            # 数据清洗层（解析后、分块前）：归一/去噪/段落去重/质量过滤，默认关闭
            if self.config.cleaner.enabled:
                from app.adapters.cleaner import DefaultCleaner

                cleaner = DefaultCleaner(
                    enabled=True,
                    normalize=self.config.cleaner.normalize,
                    normalize_punct=self.config.cleaner.normalize_punct,
                    noise=self.config.cleaner.noise,
                    dedup=self.config.cleaner.dedup,
                    quality=self.config.cleaner.quality,
                    dedup_threshold=self.config.cleaner.dedup_threshold,
                    min_chunk_chars=self.config.cleaner.min_chunk_chars,
                    paragraph_mode=self.config.cleaner.paragraph_mode,
                    max_drop_ratio=self.config.cleaner.max_drop_ratio,
                    dedup_max_paragraphs=self.config.cleaner.dedup_max_paragraphs,
                    exempt_kinds=tuple(self.config.cleaner.exempt_kinds),
                )
                # V5.2：传 kind 供按类型豁免（代码/JSON/CSV/表格类跳过 quality/dedup）
                doc.text = cleaner.clean(doc.text, kind=doc.metadata.get("kind"))
                reindex_tables(doc)   # P1-4：清洗删表后按 [TABLE] 顺序重对齐 tables JSON
                if not doc.text.strip():
                    logger.warning("清洗后文档为空（%s）：全部段落被噪声/去重/质量过滤剔除，仍按空文档入库",
                                   filename,
                                   extra={"extra_fields": {"job_id": job.job_id, "filename": filename}})

            # P-C：入库侧提示注入扫描（默认关闭，不改变现有入库行为）
            if self.config.injection.enabled:
                from app.adapters.injection_scanner import InjectionScanner

                scanner = InjectionScanner(patterns=self.config.injection.patterns,
                                           whitelist=self.config.injection.whitelist)
                injection_hits = scanner.scan(doc.text)
                if injection_hits:
                    logger.warning(
                        "提示注入扫描命中",
                        extra={"extra_fields": {"job_id": job.job_id,
                                                "hits": [h["pattern"][:60] for h in injection_hits],
                                                "snippet": injection_hits[0]["snippet"][:80]}},
                    )
                    if self.config.injection.mode == "reject":
                        raise InjectionError()
                    doc.metadata["injection_hits"] = injection_hits

            # P-C：入库前内容审核（默认关闭）
            if self.config.moderation.enabled:
                from app.adapters.content_moderation.keyword_moderator import KeywordModerator

                result = KeywordModerator(
                    keywords_csv=self.config.moderation.keywords_csv).moderate(doc.text)
                if result.verdict == "reject":
                    categories = sorted({r["category"] for r in result.matched_rules})
                    logger.warning(
                        "内容审核命中",
                        extra={"extra_fields": {"job_id": job.job_id,
                                                "categories": categories,
                                                "snippet": result.snippet[:80]}},
                    )
                    if self.config.moderation.mode == "reject":
                        raise ContentModerationError(f"命中类别：{'、'.join(categories)}")
                    doc.metadata["moderation_flags"] = result.matched_rules

            _stage("clean")   # P2-7：clean 段含 pii 脱敏/清洗/注入扫描/内容审核

            # TOCTOU 防护：同 content_hash 的并发入库串行化（per-hash 锁），
            # 幂等检查与写入之间不再存在竞态窗口
            with self._lock_for(doc.content_hash):
                # 幂等防线（API 层快速返回 duplicate 之外的第二道检查，防并发重复入库；按库隔离）
                existing = self.find_document_id_by_hash(doc.content_hash,
                                                         collection=collection, kb_id=kb_id)
                if existing and not force:
                    entry = self.documents.get(existing)
                    self.jobs.update(job.job_id, status="success", document_id=existing,
                                     chunk_count=entry.chunk_count if entry else 0)
                    metrics.inc_counter("rag_ingest_total", {"result": "duplicate"},
                                        help_text="入库结果计数（P2-7）")
                    logger.info(
                        "重复上传命中既有文档，跳过入库",
                        extra={"extra_fields": {"job_id": job.job_id, "document_id": existing,
                                                "collection": collection}},
                    )
                    return self.jobs.get(job.job_id) or job
                # V5.2 增量更新：force 命中同内容既有文档 → 强制重建并替换旧版本
                if force and existing and not replace_document_id:
                    replace_document_id = existing
                old_entry = (self.documents.get(replace_document_id)
                             if replace_document_id else None)

                # V5.2：分块 token 计量——measure=auto 时优先用引擎真实分词器（精确），
                # 引擎不支持（默认实现返回 None）或异常由 chunker 内部自动回退 ratio 估算。
                counter = None
                if self.config.chunker.measure == "auto":
                    candidate = getattr(self.embedding, "count_tokens", None)
                    if callable(candidate):
                        counter = candidate
                chunks = split_chunks(
                    doc,
                    parent_target_tokens=self.config.chunker.parent_target_tokens,
                    child_target_tokens=self.config.chunker.child_target_tokens,
                    token_char_ratio=self.config.chunker.token_char_ratio,
                    table_aware=self.config.chunker.table_aware.model_dump(),
                    hard_boundary=self.config.chunker.hard_boundary.model_dump(),
                    context_prefix=self.config.chunker.context_prefix.model_dump(),
                    token_counter=counter,
                )
                _stage("chunk")   # P2-7
                parents = [c for c in chunks if c.parent_id is None]
                # V5.2：空结果策略——0 块默认拒收（30007），杜绝"成功入库 0 块"的静默失败
                if not parents:
                    if self.config.ingestion.empty_result == "reject":
                        raise EmptyDocumentError(data={
                            "filename": filename,
                            "reason": "解析/清洗后无有效文本（空文件/扫描件无文本层/全部段落被过滤）",
                        })
                    logger.warning(
                        "分块后 0 块（empty_result=allow，按旧行为入库）",
                        extra={"extra_fields": {"job_id": job.job_id, "filename": filename}},
                    )
                batch_size = self.config.ingestion.embed_batch_size
                vectors: list[list[float]] = []
                for start in range(0, len(parents), batch_size):
                    batch = parents[start:start + batch_size]
                    vectors.extend(self.embedding.embed_documents([c.text for c in batch]))
                if len(vectors) != len(parents):
                    raise EmbeddingError(f"向量数（{len(vectors)}）与块数（{len(parents)}）不一致")
                _stage("embed")   # P2-7
                self.vector_store.add_chunks(zip(parents, vectors), collection=collection)
                # P0-2：原件归档（幂等锁内成功路径；写失败仅告警不回滚入库，归档尽力而为）
                archive_path = self._archive_original(raw, filename, kb_id, doc.document_id)
                # V5.2：增量更新——先写新版本（已完成），再替换元数据，最后清旧版本，
                # 保证替换期间查询始终有可用数据（先写后删）
                if old_entry is not None:
                    self.documents.replace(old_entry.document_id, doc,
                                           chunk_count=len(parents), archive_path=archive_path)
                    replaced_id: str | None = old_entry.document_id
                else:
                    self.documents.register(doc, chunk_count=len(parents), archive_path=archive_path)
                    replaced_id = None
                self.jobs.update(job.job_id, status="success",
                                 document_id=doc.document_id, chunk_count=len(parents),
                                 stage_timings=stage_timings, replaced_document_id=replaced_id)
                if old_entry is not None:
                    # 旧版本清理（尽力而为；失败仅告警，`python -m app.cli.doctor` 可巡检）
                    try:
                        self.vector_store.delete_document(old_entry.document_id, collection=collection)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("旧版本向量清理失败（可执行 doctor 巡检）：%s（%s）",
                                       old_entry.document_id, exc)
                    self.documents.remove_archive(old_entry.archive_path)
                    logger.info(
                        "增量更新：旧版本已被替换",
                        extra={"extra_fields": {"job_id": job.job_id,
                                                "new_document_id": doc.document_id,
                                                "replaced_document_id": replaced_id,
                                                "collection": collection}},
                    )
                _stage("index")   # P2-7
                self.jobs.update(job.job_id, status="success",
                                 document_id=doc.document_id, chunk_count=len(parents),
                                 stage_timings=stage_timings)
                metrics.inc_counter("rag_ingest_total", {"result": "success"},
                                    help_text="入库结果计数（P2-7）")
                logger.info(
                    "入库成功",
                    extra={"extra_fields": {"job_id": job.job_id, "document_id": doc.document_id,
                                            "chunk_count": len(parents), "filename": filename,
                                            "collection": collection}},
                )
        except ParseError as exc:
            self.jobs.update(job.job_id, status="failed", error_code=str(exc.code))
            logger.warning("入库失败：解析错误", extra={"extra_fields": {"job_id": job.job_id, "error_code": exc.code}})
        except EmbeddingError as exc:
            self.jobs.update(job.job_id, status="failed", error_code=str(exc.code))
            logger.warning("入库失败：向量化错误", extra={"extra_fields": {"job_id": job.job_id, "error_code": exc.code}})
        except InjectionError as exc:
            # P-C：提示注入 reject（40002）
            self.jobs.update(job.job_id, status="failed", error_code=str(exc.code))
            logger.warning("入库失败：提示注入命中", extra={"extra_fields": {"job_id": job.job_id, "error_code": exc.code}})
        except ContentModerationError as exc:
            # P-C：内容审核 reject（40003）
            self.jobs.update(job.job_id, status="failed", error_code=str(exc.code))
            logger.warning("入库失败：内容审核未通过", extra={"extra_fields": {"job_id": job.job_id, "error_code": exc.code}})
        except QualityGateError as exc:
            # P0-1：解析质量门禁 reject（30005，疑似扫描件/损坏文件）
            self.jobs.update(job.job_id, status="failed", error_code=str(exc.code))
            logger.warning("入库失败：解析质量不达标（疑似扫描件/损坏文件）",
                           extra={"extra_fields": {"job_id": job.job_id, "error_code": exc.code,
                                                   "quality_flags": (exc.data or {}).get("quality_flags")}})
        except EmptyDocumentError as exc:
            # V5.2：0 块拒收（30007；空文件/扫描件/清洗过滤殆尽）
            self.jobs.update(job.job_id, status="failed", error_code=str(exc.code))
            logger.warning("入库失败：未提取到有效文本",
                           extra={"extra_fields": {"job_id": job.job_id, "error_code": exc.code,
                                                   "detail": exc.data}})
        except Exception as exc:
            self.jobs.update(job.job_id, status="failed", error_code="50001")
            logger.exception("入库失败：未知错误", extra={"extra_fields": {"job_id": job.job_id}})
        finally:
            final = self.jobs.get(job.job_id) or job
            if final.status == "failed":
                code = final.error_code or "50001"
                result = "rejected" if code in ("30005", "30007", "40002", "40003") else "failed"
                metrics.inc_counter("rag_ingest_total",
                                    {"result": result, "error_code": code},
                                    help_text="入库结果计数（P2-7）")
        return self.jobs.get(job.job_id) or job
