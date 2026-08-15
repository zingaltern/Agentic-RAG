"""上传入库流水线（DEVELOPMENT.md 5.10）。

parse_file → split_chunks → (parent chunks) embed_documents(batch) → vector_store.add_chunks
- 后台单线程 Worker（ThreadPoolExecutor max_workers=2），不占 HTTP worker 线程。
- 入库只存父块（5.10 明确 parent chunks）；子块文本供 W3 上下文回溯，本阶段不落库。
- 幂等：content_hash 命中（Chroma 持久 + 内存双检）返回既有 document_id，不重复入库。
- 失败：Job 标记 failed + error_code（ParseError→30003 / 向量化→30002 / 其他→50001）。
"""
from __future__ import annotations

import hashlib
import io
import threading
import queue
from uuid import uuid4

from app.core.config import SystemConfig
from app.core.domain import Job
from app.core.errors import (
    ContentModerationError,
    EmbeddingError,
    InjectionError,
    ParseError,
)
from app.core.interfaces import VectorEngine, VectorStore
from app.core.logging import get_logger, set_trace_id
from app.services.documents_store import DocumentsStore
from app.services.jobs_store import JobStore
from app.adapters.chunker import split_chunks
from app.adapters.parser import parse_file

logger = get_logger("ingestion")


def compute_content_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class IngestionPipeline:
    def __init__(self, config: SystemConfig, embedding: VectorEngine,
                 vector_store: VectorStore, jobs: JobStore,
                 documents: DocumentsStore, max_workers: int = 2,
                 start_workers: bool = True):
        self.config = config
        self.embedding = embedding
        self.vector_store = vector_store
        self.jobs = jobs
        self.documents = documents
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
            raw, filename, job, kb_id, collection = item
            try:
                self._run(raw, filename, job, kb_id=kb_id, collection=collection)
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
                raw, filename, job, kb_id, collection = self._queue.get_nowait()
                self.jobs.update(job.job_id, status="failed", error_code=INTERRUPTED_CODE)
                self._queue.task_done()
            except queue.Empty:
                break
        for _ in self._workers:
            self._queue.put((None, None, None, None, None))
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
               kb_id: str = "default", collection: str | None = None) -> bool:
        """异步提交入库任务（HTTP 线程立即返回）。

        C5：队列满时拒绝提交，Job 标记 failed（error_code=43003），返回 False。
        C4：pipeline 关闭中（热切换/退出）拒绝新提交并标记 50003，防止 job 永久 pending。
        """
        if self._closing:
            from app.services.jobs_store import INTERRUPTED_CODE

            self.jobs.update(job.job_id, status="failed", error_code=INTERRUPTED_CODE)
            return False
        try:
            self._queue.put_nowait((raw, filename, job, kb_id, collection))
            return True
        except queue.Full:
            self.jobs.update(job.job_id, status="failed", error_code="43003")
            return False

    def ingest(self, raw: bytes, filename: str, job: Job | None = None,
               kb_id: str = "default", collection: str | None = None) -> Job:
        """同步入库（demo-ingest / 单测用），返回更新后的 Job。

        kb_id/collection：13.5 多知识库作用域；collection 缺省按 kb 解析（default → "kb"）。
        """
        job = job or self.jobs.create(filename, kb_id=kb_id)
        return self._run(raw, filename, job, kb_id=kb_id, collection=collection)

    def _run(self, raw: bytes, filename: str, job: Job,
             kb_id: str = "default", collection: str | None = None) -> Job:
        # worker 线程无请求上下文，生成独立 trace_id 贯穿本 Job 日志
        set_trace_id(uuid4().hex[:12])
        collection = collection or self.config.vector_store.collection
        self.jobs.update(job.job_id, status="running")
        try:
            doc = parse_file(io.BytesIO(raw), filename, config=self.config.ingestion)
            doc.metadata["kb_id"] = kb_id          # 入库时写 Chunk.metadata.kb_id（13.5）

            # S5（13.11.1）：敏感数据脱敏（身份证/手机号/银行卡），默认关闭
            if self.config.ingestion.desensitize_enabled:
                from app.services.desensitize import desensitize_text

                doc.text = desensitize_text(doc.text)

            # 数据清洗层（解析后、分块前）：归一/去噪/段落去重/质量过滤，默认关闭
            if self.config.cleaner.enabled:
                from app.adapters.cleaner import DefaultCleaner

                cleaner = DefaultCleaner(
                    enabled=True,
                    normalize=self.config.cleaner.normalize,
                    noise=self.config.cleaner.noise,
                    dedup=self.config.cleaner.dedup,
                    quality=self.config.cleaner.quality,
                    dedup_threshold=self.config.cleaner.dedup_threshold,
                    min_chunk_chars=self.config.cleaner.min_chunk_chars,
                )
                doc.text = cleaner.clean(doc.text)
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

            # TOCTOU 防护：同 content_hash 的并发入库串行化（per-hash 锁），
            # 幂等检查与写入之间不再存在竞态窗口
            with self._lock_for(doc.content_hash):
                # 幂等防线（API 层快速返回 duplicate 之外的第二道检查，防并发重复入库；按库隔离）
                existing = self.find_document_id_by_hash(doc.content_hash,
                                                         collection=collection, kb_id=kb_id)
                if existing:
                    entry = self.documents.get(existing)
                    self.jobs.update(job.job_id, status="success", document_id=existing,
                                     chunk_count=entry.chunk_count if entry else 0)
                    logger.info(
                        "重复上传命中既有文档，跳过入库",
                        extra={"extra_fields": {"job_id": job.job_id, "document_id": existing,
                                                "collection": collection}},
                    )
                    return self.jobs.get(job.job_id) or job

                chunks = split_chunks(
                    doc,
                    parent_target_tokens=self.config.chunker.parent_target_tokens,
                    child_target_tokens=self.config.chunker.child_target_tokens,
                    token_char_ratio=self.config.chunker.token_char_ratio,
                )
                parents = [c for c in chunks if c.parent_id is None]
                batch_size = self.config.ingestion.embed_batch_size
                vectors: list[list[float]] = []
                for start in range(0, len(parents), batch_size):
                    batch = parents[start:start + batch_size]
                    vectors.extend(self.embedding.embed_documents([c.text for c in batch]))
                if len(vectors) != len(parents):
                    raise EmbeddingError(f"向量数（{len(vectors)}）与块数（{len(parents)}）不一致")
                self.vector_store.add_chunks(zip(parents, vectors), collection=collection)
                self.documents.register(doc, chunk_count=len(parents))
                self.jobs.update(job.job_id, status="success",
                                 document_id=doc.document_id, chunk_count=len(parents))
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
        except Exception as exc:
            self.jobs.update(job.job_id, status="failed", error_code="50001")
            logger.exception("入库失败：未知错误", extra={"extra_fields": {"job_id": job.job_id}})
        return self.jobs.get(job.job_id) or job
