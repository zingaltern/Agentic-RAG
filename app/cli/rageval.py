"""评测 CLI（DEVELOPMENT.md 8.3、13.6）：加载 qa_pairs → 检索+问答链路 → Markdown 报告。

两种运行模式：
- 默认（HTTP）：调用已启动服务的 POST /api/query（真实 DeepSeek LLM）。
- --in-process：进程内装配（真实 BGE 嵌入 + Chroma），LLM 用 Stub（不赖外网/真 key）；
  入库 --docs 目录文档后逐条评测。

判据（13.6 修订）：
- no_answer：pass =（code==0 且 answer/message 含"未找到相关依据"类文案）∨（code==21001）。
- Hit@1 / Hit@5 / ContextPrecision / ContextRecall / AnswerRelevancy / Faithfulness（分类型）。

多库（13.5）：qa_pairs/{kb}/*.jsonl 按库评测；--kb 指定单库，--all-kbs 遍历全部子目录；
--baseline <path> 对比基线报告输出 diff 列。报告输出 reports/eval-{ts}.md。

外部基准测试集（8.4）：--kb + --pairs + --docs 显式指定独立目录（如 tests/review/，
make review 目标）；--fresh-kb 进程内评测前清空该库向量集合与文档幂等表（与 KB 删除
端点 C7 级联一致），保证每轮全新入库、结果可复现。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

CODE_FALLBACK = 21001

# 13.6 口径统一的"未找到依据"文案集合（判定用单一常量 + 单测）
NO_ANSWER_PHRASES = (
    "未找到相关依据",
    "未找到相关答案",
    "知识库中未找到",
    "无法找到相关",
)


def no_answer_passed(code: int, text: str) -> bool:
    """13.6 修订判据：code0+未找到文案 ∨ 21001。"""
    if code == CODE_FALLBACK:
        return True
    if code == 0:
        return any(p in text for p in NO_ANSWER_PHRASES)
    return False


@dataclass
class CaseResult:
    id: str
    kind: str
    query: str
    variant: str = ""          # 特征标记：spoken/table/multi_doc/""（13.6）
    ok: bool = False
    code: int = 0
    answer: str = ""
    sources: list[str] = field(default_factory=list)
    iterations: int = -1
    hallucination_check: str = ""
    hit: bool = False
    hit1: bool = False
    context_recall: float = 0.0
    context_precision: float = 0.0
    answer_relevancy: bool = False
    faithful: bool = False
    elapsed_ms: int = 0
    contexts: list[dict] = field(default_factory=list)   # P0：完整上下文（--export-jsonl/质量评测用）
    expected_sources: list[str] = field(default_factory=list)
    reference_answer: str = ""
    expected_behavior: str = ""
    error: str = ""


def load_pairs(path: Path) -> list[dict]:
    """支持 .jsonl（每行一条）与 .json（{qa_pairs: []}）。"""
    if path.suffix == ".jsonl":
        pairs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pairs = raw.get("qa_pairs") if isinstance(raw, dict) else raw
    if not isinstance(pairs, list) or not pairs:
        sys.exit(f"qa_pairs 格式不符：缺少非空用例数组（{path}）")
    return pairs


def call_query(base_url: str, query: str, kb_id: str | None = None,
               token: str | None = None) -> dict:
    payload: dict = {"query": query, "top_k": 5, "debug": True}
    if kb_id:
        payload["kb_id"] = kb_id
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base_url}/api/query",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def login_and_get_token(base_url: str, username: str, password: str) -> str:
    """V5.1：现场登录换取 token（--login <user> <pass>）。"""
    req = urllib.request.Request(
        f"{base_url}/api/auth/login",
        data=json.dumps({"username": username, "password": password}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("code") != 0:
        sys.exit(f"登录失败（{base_url}）：{body.get('message')}")
    return body["data"]["token"]


# -- 进程内模式 ---------------------------------------------------------------

class _StubLLM:
    """固定答案（带 [1] 引用），用于不依赖外网的检索链路评测。"""

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "根据知识库上下文，请参考编号 [1] 对应来源。"


def _doc_files(docs_dir: Path) -> list[Path]:
    """P0：进程内评测入库文件列表——跳过隐藏文件与不支持扩展（如 .DS_Store）。"""
    from app.adapters.parser.formats import SUPPORTED_EXTENSIONS

    return sorted(p for p in docs_dir.iterdir()
                  if p.is_file() and not p.name.startswith(".")
                  and p.suffix.lower() in SUPPORTED_EXTENSIONS)


def _run_in_process(config, docs_dir: Path, pairs: list[dict],
                    kb_id: str = "default", debug: bool = False,
                    fresh: bool = False, capture_contexts: bool = False,
                    llm=None, embedding=None, reranker=None) -> list[dict]:
    """进程内评测；llm/embedding/reranker 可注入（默认 Stub LLM + 真实 BGE/Chroma）。"""
    from app.core.config import load_config
    from app.core.registry import wire
    from app.services.kb_store import DEFAULT_KB_ID, KbStore

    config = config or load_config()
    ks = KbStore()
    if kb_id != DEFAULT_KB_ID and not ks.exists(kb_id):
        ks.create(kb_id)                     # 物理集合随评测创建（进程内隔离）
    collection = ks.collection_of(kb_id)
    deps = wire(config, llm=llm or _StubLLM(), embedding=embedding, reranker=reranker, kbs=ks)
    if fresh and collection and deps.vector_store is not None:
        # 8.4 --fresh-kb：评测前清空该库（集合 + 文档幂等表），保证全新入库可复现；
        # 不清幂等表会因 content_hash 命中跳过重嵌、块却已删 → 检索必空
        deps.vector_store.drop_collection(collection)
        if deps.documents is not None:
            deps.documents.delete_by_kb(kb_id)

    for path in _doc_files(docs_dir):
        job = deps.pipeline.ingest(path.read_bytes(), path.name,
                                   kb_id=kb_id, collection=collection)
        if job.status != "success":
            print(f"  ⚠️ 入库失败 {path.name}: {job.error_code}", file=sys.stderr)

    # 暖机：模型加载与 BM25 索引构建属装配开销，不计入检索预算
    deps.embedding.ready()
    deps.retriever.retrieve("暖机查询", k=1, collection=collection)

    out = []
    for pair in pairs:
        started = time.time()
        try:
            result = deps.orchestration.run(pair["query"], top_k=5,
                                            collection=collection, debug=debug,
                                            capture_contexts=capture_contexts)
            elapsed = round((time.time() - started) * 1000)
            # 与 HTTP 模式一致的响应体形状：{code, data, elapsed_ms, message}
            detection_rate = result.hallucination.verified_rate if result.hallucination else None
            out.append({
                "code": result.code,
                "message": result.message,
                "data": {
                    "answer": result.text,
                    "sources": result.sources,
                    "contexts": result.contexts,
                    "iterations": result.iterations,
                    "hallucination_check": "passed"
                    if (result.hallucination and result.hallucination.verified) else "suspected",
                    "detection_rate": detection_rate,
                },
                "elapsed_ms": elapsed,
            })
        except Exception as exc:  # noqa: BLE001
            out.append({"code": 50001, "data": {}, "message": f"{type(exc).__name__}: {exc}",
                        "elapsed_ms": 0})
    return out


# -- 指标 ----------------------------------------------------------------------

def eval_pair(pair: dict, response: dict) -> CaseResult:
    r = CaseResult(id=pair["id"], kind=pair["type"], query=pair["query"],
                   variant=pair.get("variant", ""))
    kind = pair["type"]
    r.expected_sources = list(pair.get("expected_sources") or pair.get("expected_doc") or [])
    r.reference_answer = (pair.get("reference_answer") or pair.get("golden_answer")
                          or pair.get("golden") or "")
    r.expected_behavior = pair.get("expected_behavior", "") or ""
    expected = set(r.expected_sources)
    r.code = response.get("code", 50001)
    data = response.get("data", {}) or {}
    r.answer = data.get("answer", "") or ""
    r.sources = [s.get("filename", "") for s in data.get("sources", [])]
    r.contexts = data.get("contexts", []) or []
    if not r.sources and r.contexts:
        # 兜底/澄清路径只有 contexts：IR 不应把“生成兜底”误记为“检索未命中”
        r.sources = [str(c.get("filename", "")) for c in r.contexts if c.get("filename")]
    r.iterations = data.get("iterations", -1)
    r.hallucination_check = data.get("hallucination_check", "")
    # P0：兼容进程内（顶层 elapsed_ms）与 HTTP（data.elapsed_ms）两种响应形状
    r.elapsed_ms = data.get("elapsed_ms") or response.get("elapsed_ms") or 0
    r.faithful = r.hallucination_check == "passed"

    if kind in ("direct", "rewrite"):
        r.hit1 = bool(expected) and bool(expected & set(r.sources[:1]))
        r.hit = bool(expected) and bool(expected & set(r.sources[:5]))
        r.context_recall = (len(expected & set(r.sources)) / len(expected)) if expected else 0.0
        if expected:
            precisions = [1.0 / (i + 1) for i, s in enumerate(r.sources) if s in expected]
            r.context_precision = round(sum(precisions) / len(expected), 4) if precisions else 0.0
        r.answer_relevancy = bool(r.answer.strip()) and "抱歉，暂未找到" not in r.answer
        r.ok = r.hit
        if not r.ok:
            r.error = f"期望来源 {sorted(expected)} 未命中（实际 {r.sources[:5]}）"
    elif kind == "no_answer":
        # 13.6 修订判据：进程内兜底可能无 answer 键，回退读 message
        text = (r.answer + " " + (data.get("message") or response.get("message") or "")).strip()
        r.ok = no_answer_passed(r.code, text)
        if not r.ok and r.code == 0:
            # 进程内 Stub 模式补强：回答与上下文完全无支撑（verified_rate=0）→ 视同"未找到依据"拒答
            rate = data.get("detection_rate")
            r.ok = r.hallucination_check == "suspected" and rate == 0.0
        r.answer_relevancy = r.ok
        if not r.ok:
            r.error = f"期望兜底/未找到文案，实际 code={r.code} answer={r.answer[:40]!r}"
    elif kind == "no_invent":
        r.ok = no_answer_passed(r.code, r.answer + " " + (data.get("message") or "")) or \
               (not r.faithful) or ("未" in r.answer[:80] or "没有" in r.answer[:80])
        r.answer_relevancy = bool(r.answer.strip())
        if not r.ok:
            r.error = "疑似编造（回答未提示无依据）"
    else:
        r.error = f"未知 type: {kind}"
    return r


def _aggregate(results: list[CaseResult]) -> dict:
    direct = [r for r in results if r.kind in ("direct", "rewrite")]
    no_ans = [r for r in results if r.kind == "no_answer"]
    agg = {
        "total": len(results),
        "ok": sum(1 for r in results if r.ok),
        "hit1": (sum(1 for r in direct if r.hit1) / len(direct)) if direct else 0.0,
        "hit5": (sum(1 for r in direct if r.hit) / len(direct)) if direct else 0.0,
        "context_precision": (sum(r.context_precision for r in direct) / len(direct)) if direct else 0.0,
        "context_recall": (sum(r.context_recall for r in direct) / len(direct)) if direct else 0.0,
        "answer_relevancy": (sum(1 for r in direct if r.answer_relevancy) / len(direct)) if direct else 0.0,
        "faithfulness": (sum(1 for r in direct if r.faithful) / len(direct)) if direct else 0.0,
        "faithfulness_direct": (sum(1 for r in direct if r.faithful) / len(direct)) if direct else 0.0,
        "fallback_rate": (sum(1 for r in no_ans if r.ok) / len(no_ans)) if no_ans else 0.0,
    }
    return agg


def _parse_baseline(path: Path) -> dict[str, float]:
    """解析基线报告 Markdown 的聚合表（| 指标 | 值 |）。

    P0：支持百分比（`90.00%`）与计数比（`18/21`）两种值形态；
    后者用于“通过率”行（修复此前基线 diff 拿不到通过率的问题）。
    """
    out: dict[str, float] = {}
    if not path.exists():
        return out
    in_diff = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            # 基线报告本身可能含“与基线 diff”表：跳过，避免把现状当基线
            in_diff = "diff" in line.lower()
            continue
        if in_diff:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        name, value = cells[0], cells[1]
        if not name or not value:
            continue
        if name in out:
            continue  # 报告含多库聚合表：只取第一张（与 render_report 的 diff 口径一致）
        if value.endswith("%"):
            try:
                out[name] = float(value[:-1]) / 100.0
            except ValueError:
                continue
        elif re.fullmatch(r"\d+\s*/\s*\d+", value):
            num, den = (int(x) for x in value.split("/"))
            if den:
                out[name] = num / den
    return out


def _diff_text(now: float, base: float | None) -> str:
    if base is None:
        return "新指标"
    delta = now - base
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.0%}"

# -- 报告 ----------------------------------------------------------------------

def _metric_values(agg: dict) -> dict[str, float]:
    """聚合指标 → 可比较的 0~1 数值（用于基线 diff；P0 修复通过率不可比问题）。"""
    total = agg["total"] or 1
    return {
        "通过率": agg["ok"] / total,
        "Hit@1": agg["hit1"],
        "Hit@5": agg["hit5"],
        "ContextPrecision": agg["context_precision"],
        "ContextRecall": agg["context_recall"],
        "AnswerRelevancy": agg["answer_relevancy"],
        "Faithfulness": agg["faithfulness"],
        "Faithfulness(direct)": agg["faithfulness_direct"],
        "兜底率": agg["fallback_rate"],
    }


def _agg_rows(agg: dict) -> list[tuple[str, str]]:
    return [
        ("通过率", f"{agg['ok']}/{agg['total']}"),
        ("Hit@1", f"{agg['hit1']:.2%}"),
        ("Hit@5", f"{agg['hit5']:.2%}"),
        ("ContextPrecision", f"{agg['context_precision']:.2%}"),
        ("ContextRecall", f"{agg['context_recall']:.2%}"),
        ("AnswerRelevancy", f"{agg['answer_relevancy']:.2%}"),
        ("Faithfulness", f"{agg['faithfulness']:.2%}"),
        ("Faithfulness(direct)", f"{agg['faithfulness_direct']:.2%}"),
        ("兜底率", f"{agg['fallback_rate']:.2%}"),
    ]


def render_report(args, kb_results: dict[str, list[CaseResult]],
                  kb_aggs: dict[str, dict], baseline: dict[str, float] | None = None) -> str:
    lines = [
        "# RAG 评测报告",
        "",
        f"- 模式：{'进程内（Stub LLM，不依赖外网）' if args.in_process else f'HTTP {args.base_url}'}",
        f"- 库：{', '.join(kb_results)}",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    if baseline:
        lines += ["## 与基线 diff（13.6 对比模式）", "",
                  "| 指标 | 基线 | 现状 | Δ |", "|---|---|---|---|"]
        for name, value in _metric_values(next(iter(kb_aggs.values()))).items():
            base = baseline.get(name)
            lines.append(f"| {name} | {base:.0%} | {value:.0%} | {_diff_text(value, base)} |"
                         if base is not None else f"| {name} | - | {value:.0%} | 新指标 |")
        lines.append("")

    # 按库汇总表（13.6）
    lines += ["## 按库汇总", "", "| 库 | 条数 | 通过 | Hit@1 | Hit@5 | Faithfulness | 兜底率 |",
              "|---|---|---|---|---|---|---|"]
    for kb, agg in kb_aggs.items():
        lines.append(f"| {kb} | {agg['total']} | {agg['ok']} | {agg['hit1']:.0%} | {agg['hit5']:.0%} "
                     f"| {agg['faithfulness_direct']:.0%} | {agg['fallback_rate']:.0%} |")
    total_agg = _merge_aggs(list(kb_aggs.values()))
    lines.append(f"| **合计** | {total_agg['total']} | {total_agg['ok']} | "
                 f"{total_agg['hit1']:.0%} | {total_agg['hit5']:.0%} | "
                 f"{total_agg['faithfulness_direct']:.0%} | {total_agg['fallback_rate']:.0%} |")
    lines.append("")

    for kb, results in kb_results.items():
        agg = kb_aggs[kb]
        lines += [f"## 库 {kb} 聚合", "",
                  "| 指标 | 值 |", "|---|---|"]
        for name, value in _agg_rows(agg):
            lines.append(f"| {name} | {value} |")
        # 特征分布（spoken/table/multi_doc，13.6）
        variants: dict[str, list[CaseResult]] = {}
        for r in results:
            variants.setdefault(r.variant or "default", []).append(r)
        if len(variants) > 1:
            lines += ["", "### 特征分布", "", "| 特征 | 条数 | 通过率 |", "|---|---|---|"]
            for v, rs in sorted(variants.items()):
                ok = sum(1 for r in rs if r.ok)
                lines.append(f"| {v} | {len(rs)} | {ok}/{len(rs)} ({ok / len(rs):.0%}) |")
        lines += ["", "## 逐条明细", "",
                  "| ID | 类型 | 特征 | OK | Hit@1 | Hit@5 | C-Prec | C-Rec | 迭代 | 幻觉 | 耗时(ms) | 说明 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in results:
            mark = "PASS" if r.ok else "FAIL"
            lines.append(
                f"| {r.id} | {r.kind} | {r.variant or '-'} | {mark} | {'Y' if r.hit1 else '-'} "
                f"| {'Y' if r.hit else '-'} | {r.context_precision:.2f} | {r.context_recall:.2f} "
                f"| {r.iterations} | {r.hallucination_check or '-'} | {r.elapsed_ms} "
                f"| {r.error or r.answer[:36]} |"
            )
        failed = [r for r in results if not r.ok]
        lines += ["", "## 已知低分项（如实列出）", ""]
        if failed:
            for r in failed:
                lines.append(f"- **{r.id}**（{r.kind}）`{r.query}`：{r.error}")
        else:
            lines.append("- 无（全部通过）")
        lines.append("")
    return "\n".join(lines)


def _merge_aggs(aggs: list[dict]) -> dict:
    out = {"total": 0, "ok": 0, "hit1": 0.0, "hit5": 0.0, "context_precision": 0.0,
           "context_recall": 0.0, "answer_relevancy": 0.0, "faithfulness": 0.0,
           "faithfulness_direct": 0.0, "fallback_rate": 0.0}
    n = max(1, len(aggs))
    for a in aggs:
        for key in out:
            if key in ("total", "ok"):
                out[key] += a[key]
            else:
                out[key] += a[key] / n
    return out


def _default_pairs(kb: str) -> str:
    return f"tests/eval/qa_pairs/{kb}/qa_pairs.jsonl"


def _default_docs(kb: str) -> str:
    if kb == "default":
        return "tests/eval/sample_data"
    return f"tests/eval/sample_data/{kb}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Agentic RAG 评测（8.3 / 13.6）")
    parser.add_argument("--pairs", default=None, help="用例文件（jsonl/json），缺省按 --kb 解析")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", default=None, help="报告路径，默认 reports/eval-{timestamp}.md")
    parser.add_argument("--in-process", action="store_true",
                        help="进程内模式：真实 BGE+Chroma，LLM 用 Stub（不依赖外网/服务）")
    parser.add_argument("--docs", default=None, help="进程内模式要入库的文档目录")
    parser.add_argument("--kb", default=None, help="按库评测（default/docs/...）")
    parser.add_argument("--all-kbs", action="store_true",
                        help="遍历 tests/eval/qa_pairs/ 下全部库目录评测")
    parser.add_argument("--baseline", default=None, help="基线报告路径（对比 diff 列）")
    parser.add_argument("--fresh-kb", action="store_true",
                        help="进程内模式：评测前清空该 kb 的向量集合与文档幂等表（全新入库，8.4）")
    parser.add_argument("--token", default=None, help="HTTP 模式：Bearer token（认证启用时）")
    parser.add_argument("--login", nargs=2, metavar=("USER", "PASS"),
                        help="HTTP 模式：现场登录换取 token")
    parser.add_argument("--export-jsonl", default=None,
                        help="P0：每案原始结果（含完整 contexts/gold）导出为 JSONL；仅支持 --in-process")
    args = parser.parse_args()
    if args.export_jsonl and not args.in_process:
        parser.error("--export-jsonl 仅支持 --in-process（HTTP 响应不含完整 contexts）")
    if args.fresh_kb and (args.kb == "default" or args.all_kbs):
        parser.error("拒绝 --fresh-kb 作用于 default/--all-kbs：会清空生产默认库")

    from app.core.config import load_config

    config = load_config()
    base_dir = Path("tests/eval/qa_pairs")
    if args.all_kbs:
        kbs = sorted(d.name for d in base_dir.iterdir()
                     if d.is_dir() and (d / "qa_pairs.jsonl").exists())
    elif args.kb:
        kbs = [args.kb]
    else:
        kbs = ["default"]

    kb_results: dict[str, list[CaseResult]] = {}
    kb_aggs: dict[str, dict] = {}
    for kb in kbs:
        pairs_path = Path(args.pairs) if (args.pairs and len(kbs) == 1) else Path(_default_pairs(kb))
        pairs = load_pairs(pairs_path)
        docs = Path(args.docs) if (args.docs and len(kbs) == 1) else Path(_default_docs(kb))
        if args.in_process:
            responses = _run_in_process(config, docs, pairs, kb_id=kb, fresh=args.fresh_kb,
                                        capture_contexts=bool(args.export_jsonl))
        else:
            token = args.token
            if args.login:
                token = login_and_get_token(args.base_url, args.login[0], args.login[1])
            responses = []
            for pair in pairs:
                try:
                    responses.append(call_query(args.base_url, pair["query"], kb_id=kb, token=token))
                except urllib.error.HTTPError as exc:
                    responses.append({"code": exc.code, "data": {}, "message": f"HTTP {exc.code}"})
                except Exception as exc:  # noqa: BLE001
                    responses.append({"code": 50001, "data": {},
                                      "message": f"{type(exc).__name__}: {exc}"})
        results = [eval_pair(p, resp) for p, resp in zip(pairs, responses)]
        kb_results[kb] = results
        kb_aggs[kb] = _aggregate(results)

    if args.export_jsonl:
        export_path = Path(args.export_jsonl)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8") as fh:
            for kb, results in kb_results.items():
                for r in results:
                    record = asdict(r)
                    record["kb_id"] = kb
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"原始结果已导出：{export_path}")

    baseline = _parse_baseline(Path(args.baseline)) if args.baseline else None
    out = Path(args.out or f"reports/eval-{time.strftime('%Y%m%d-%H%M%S')}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(args, kb_results, kb_aggs, baseline), encoding="utf-8")
    total = _merge_aggs(list(kb_aggs.values()))
    print(f"报告已生成：{out}（{len(out.read_text(encoding='utf-8'))} 字节）")
    print(f"合计：{total['ok']}/{total['total']} 通过，Hit@1={total['hit1']:.0%}，"
          f"Hit@5={total['hit5']:.0%}，Faithfulness(direct)={total['faithfulness_direct']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
