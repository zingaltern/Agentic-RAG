"""评测 CLI（DEVELOPMENT.md 8.3）：加载 qa_pairs.json → 检索+问答链路 → Markdown 报告。

两种运行模式：
- 默认（HTTP）：调用已启动服务的 POST /api/query（真实 DeepSeek LLM）。
- --in-process：进程内装配（真实 BGE 嵌入 + Chroma），LLM 用 Stub 返回固定答案（不赖外网/真 key）；
  入库 --docs 目录下的文档后逐条评测，适合离线回归检索质量。

指标：Hit@5 / ContextPrecision / ContextRecall / AnswerRelevancy / Faithfulness（可选，LLM 失败不阻塞）。
输出：reports/eval-{timestamp}.md（逐条 + 聚合；reports/ 已 gitignore）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CODE_FALLBACK = 21001


@dataclass
class CaseResult:
    id: str
    kind: str
    query: str
    ok: bool = False
    code: int = 0
    answer: str = ""
    sources: list[str] = field(default_factory=list)
    iterations: int = -1
    hallucination_check: str = ""
    hit: bool = False
    context_recall: float = 0.0
    context_precision: float = 0.0
    answer_relevancy: bool = False
    faithful: bool = False
    elapsed_ms: int = 0
    error: str = ""


def load_pairs(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    pairs = raw.get("qa_pairs")
    if not isinstance(pairs, list) or not pairs:
        sys.exit(f"qa_pairs.json 格式不符：缺少非空 qa_pairs 数组（{path}）")
    return pairs


def call_query(base_url: str, query: str) -> dict:
    payload = {"query": query, "top_k": 5, "debug": True}
    req = urllib.request.Request(
        f"{base_url}/api/query",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# -- 进程内模式 ---------------------------------------------------------------

class _StubLLM:
    """固定答案（带 [1] 引用），用于不依赖外网的检索链路评测。"""

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "根据知识库上下文，请参考编号 [1] 对应来源。"  # 引用 [1] 便于验证引用链路


def _run_in_process(config, docs_dir: Path, pairs: list[dict], debug: bool = False) -> list[dict]:
    from app.core.config import load_config
    from app.core.registry import wire

    config = config or load_config()
    deps = wire(config, llm=_StubLLM())
    files = sorted(p for p in (docs_dir or Path("tests/eval/sample_data")).iterdir() if p.is_file())
    for path in files:
        job = deps.pipeline.ingest(path.read_bytes(), path.name)
        if job.status != "success":
            print(f"  ⚠️ 入库失败 {path.name}: {job.error_code}", file=sys.stderr)

    # 暖机：模型加载与 BM25 索引构建属装配开销，不计入检索预算（避免冷启动误超时）
    deps.embedding.ready()
    deps.retriever.retrieve("暖机查询", k=1)

    out = []
    for pair in pairs:
        started = time.time()
        try:
            result = deps.orchestration.run(pair["query"], top_k=5, debug=debug)
            elapsed = round((time.time() - started) * 1000)
            # 与 HTTP 模式一致的响应体形状：{code, data, elapsed_ms}
            out.append({
                "code": result.code,
                "data": {
                    "answer": result.text,
                    "sources": [{"filename": s["filename"]} for s in result.sources],
                    "iterations": result.iterations,
                    "hallucination_check": "passed"
                    if (result.hallucination and result.hallucination.verified) else "suspected",
                },
                "elapsed_ms": elapsed,
            })
        except Exception as exc:  # noqa: BLE001
            out.append({"code": 50001, "data": {},
                        "elapsed_ms": 0, "error": f"{type(exc).__name__}: {exc}"})
    return out


# -- 指标 ----------------------------------------------------------------------

def eval_pair(pair: dict, response: dict) -> CaseResult:
    r = CaseResult(id=pair["id"], kind=pair["type"], query=pair["query"])
    kind = pair["type"]
    expected = set(pair.get("expected_sources", []))
    r.code = response.get("code", 50001)
    data = response.get("data", {}) or {}
    r.answer = data.get("answer", "") or ""
    r.sources = [s.get("filename", "") for s in data.get("sources", [])]
    r.iterations = data.get("iterations", -1)
    r.hallucination_check = data.get("hallucination_check", "")
    r.elapsed_ms = response.get("elapsed_ms") or 0
    r.faithful = r.hallucination_check == "passed"

    if kind in ("direct", "rewrite"):
        # Hit@5：期望来源出现在 top5 来源中
        r.hit = bool(expected) and bool(expected & set(r.sources[:5]))
        # ContextRecall：期望来源的命中比例
        r.context_recall = (len(expected & set(r.sources)) / len(expected)) if expected else 0.0
        # ContextPrecision（文档级 AP 近似）：期望来源位置倒数均值
        if expected:
            precisions = [1.0 / (i + 1) for i, s in enumerate(r.sources) if s in expected]
            r.context_precision = round(sum(precisions) / len(expected), 4) if precisions else 0.0
        # AnswerRelevancy：非空 + 非兜底文案
        r.answer_relevancy = bool(r.answer.strip()) and "抱歉，暂未找到" not in r.answer
        r.ok = r.hit
        if not r.ok:
            r.error = f"期望来源 {sorted(expected)} 未命中（实际 {r.sources[:5]}）"
    elif kind == "no_answer":
        r.ok = r.code == CODE_FALLBACK
        r.answer_relevancy = r.ok
        if not r.ok:
            r.error = f"期望兜底({CODE_FALLBACK})，实际 code={r.code}"
    elif kind == "no_invent":
        # 防幻觉探针：兜底、检测未通过、或回答明示无依据 → 通过
        r.ok = (r.code == CODE_FALLBACK) or (not r.faithful) or \
               ("未" in r.answer[:80] or "没有" in r.answer[:80])
        r.answer_relevancy = bool(r.answer.strip())
        if not r.ok:
            r.error = "疑似编造（回答未提示无依据）"
    else:
        r.error = f"未知 type: {kind}"
    return r


def _aggregate(results: list[CaseResult]) -> dict:
    direct = [r for r in results if r.kind in ("direct", "rewrite")]
    agg = {
        "total": len(results),
        "ok": sum(1 for r in results if r.ok),
        "hit5": (sum(1 for r in direct if r.hit) / len(direct)) if direct else 0.0,
        "context_precision": (sum(r.context_precision for r in direct) / len(direct)) if direct else 0.0,
        "context_recall": (sum(r.context_recall for r in direct) / len(direct)) if direct else 0.0,
        "answer_relevancy": (sum(1 for r in direct if r.answer_relevancy) / len(direct)) if direct else 0.0,
        "faithfulness": (sum(1 for r in direct if r.faithful) / len(direct)) if direct else 0.0,
        "fallback_rate": (sum(1 for r in results if r.kind == "no_answer" and r.ok)
                          / max(1, sum(1 for r in results if r.kind == "no_answer"))),
    }
    return agg


def render_report(args, results: list[CaseResult], agg: dict) -> str:
    lines = [
        "# RAG 评测报告",
        "",
        f"- 数据：`{args.pairs}`（{len(results)} 条）",
        f"- 模式：{'进程内（Stub LLM，不依赖外网）' if args.in_process else f'HTTP {args.base_url}'}",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 聚合指标",
        "",
        "| 指标 | 值 | 说明 |",
        "|---|---|---|",
        f"| 通过率 | {agg['ok']}/{agg['total']} | 各类型 OK 判据之和 |",
        f"| Hit@5 | {agg['hit5']:.2%} | 期望来源出现在 top5 检索结果 |",
        f"| ContextPrecision | {agg['context_precision']:.2%} | 相关文档位置倒数（文档级 AP 近似） |",
        f"| ContextRecall | {agg['context_recall']:.2%} | 期望来源命中比例 |",
        f"| AnswerRelevancy | {agg['answer_relevancy']:.2%} | 回答非空且非兜底文案 |",
        f"| Faithfulness | {agg['faithfulness']:.2%} | 幻觉检测 passed 比例（direct/rewrite） |",
        f"| 兜底率 | {agg['fallback_rate']:.2%} | no_answer 类按预期返回 21001 |",
        "",
        "## 逐条明细",
        "",
        "| ID | 类型 | OK | Hit@5 | C-Prec | C-Rec | 迭代 | 幻觉 | 耗时(ms) | 说明 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(
            f"| {r.id} | {r.kind} | {mark} | {'Y' if r.hit else '-'} | "
            f"{r.context_precision:.2f} | {r.context_recall:.2f} | {r.iterations} | "
            f"{r.hallucination_check or '-'} | {r.elapsed_ms} | {r.error or r.answer[:40]} |"
        )
    failed = [r for r in results if not r.ok]
    lines += ["", "## 已知低分项（如实列出，供检索/改写调优）", ""]
    if failed:
        for r in failed:
            lines.append(f"- **{r.id}**（{r.kind}）`{r.query}`：{r.error}")
    else:
        lines.append("- 无（全部通过）")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Agentic RAG 评测（8.3）")
    parser.add_argument("--pairs", default="tests/eval/qa_pairs.json")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", default=None,
                        help="报告路径，默认 reports/eval-{timestamp}.md")
    parser.add_argument("--in-process", action="store_true",
                        help="进程内模式：真实 BGE+Chroma，LLM 用 Stub（不依赖外网/服务）")
    parser.add_argument("--docs", default=None, help="进程内模式要入库的文档目录")
    args = parser.parse_args()

    pairs = load_pairs(Path(args.pairs))
    if args.in_process:
        from app.core.config import load_config

        config = load_config()
        responses = _run_in_process(config, Path(args.docs) if args.docs else None, pairs)
    else:
        responses = []
        for pair in pairs:
            try:
                responses.append(call_query(args.base_url, pair["query"]))
            except urllib.error.HTTPError as exc:
                responses.append({"code": exc.code, "data": {}, "error": f"HTTP {exc.code}"})
            except Exception as exc:  # noqa: BLE001
                responses.append({"code": 50001, "data": {}, "error": f"{type(exc).__name__}: {exc}"})

    results = [eval_pair(p, resp) for p, resp in zip(pairs, responses)]
    agg = _aggregate(results)
    out = Path(args.out or f"reports/eval-{time.strftime('%Y%m%d-%H%M%S')}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(args, results, agg), encoding="utf-8")
    print(f"报告已生成：{out}（{len(out.read_text(encoding='utf-8'))} 字节）")
    print(f"Hit@5={agg['hit5']:.0%} 通过率={agg['ok']}/{agg['total']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
