"""质量评测 CLI（P0-P2 落地）：统一 schema 用例 → 进程内检索/问答 → IR + Judge 指标 → 报告。

设计稿：`docs/specs/测试与评测体系设计.md`（P0/P2）。
定位补充（不替换）现有 `make eval`：
- `make eval`：197 条检索/兜底回归（Stub LLM，快，作为日常门禁）。
- `make eval-quality`：质量集 smoke，输出 IR 指标；`--judge` 时额外调 LLM 裁判。

用法：
    python -m app.cli.eval_quality --pairs tests/quality/datasets/smoke_quality.jsonl \
        --docs tests/quality/fixtures --kb quality_smoke --fresh-kb \
        --out reports/quality-smoke.md
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.eval.metrics import aggregate_retrieval, bootstrap_ci, mean
from app.eval.schema import EvalCase, load_cases, validate_cases


def _retrieved_ids(case: EvalCase, response: dict) -> list[str]:
    """优先 chunk_id，其次 contexts/sources 的 filename。

    兜底/澄清路径的 Answer 只有 contexts（sources 为空），因此必须从 contexts
    回退取 filename，否则“检索命中但生成兜底”会被误记为检索失败。
    """
    data = response.get("data", {}) or {}
    contexts = data.get("contexts", []) or []
    if contexts:
        if case.gold_evidence_ids:
            ids = [str(c.get("chunk_id", "")) for c in contexts if c.get("chunk_id")]
            if ids:
                return ids
        filenames = [str(c.get("filename", "")) for c in contexts if c.get("filename")]
        if filenames:
            return filenames
    return [str(s.get("filename", "")) for s in data.get("sources", [])]


def _gold_ids(case: EvalCase) -> list[str]:
    """有 chunk 级证据用 chunk_id；否则用 expected_sources 文件名。"""
    return case.gold_evidence_ids or list(case.expected_sources)


def _refusal_passed(case: EvalCase, response: dict, generation_is_stub: bool = True) -> bool:
    """兜底判据：21001/未找到文案；仅 Stub 生成时允许 detected=0 的拒答复现。

    真实 LLM 模式下，答案“无引用 + suspected”不等于明确拒答，不能计为兜底成功。
    """
    from app.cli.rageval import no_answer_passed

    data = response.get("data", {}) or {}
    text = (data.get("answer", "") or "") + " " + (response.get("message", "") or "")
    if no_answer_passed(int(response.get("code", 50001)), text):
        return True
    if not generation_is_stub:
        return False
    return data.get("hallucination_check") == "suspected" and data.get("detection_rate") == 0.0


def _build_llm(config):
    """按配置装配真实 LLM（与 wire 同 registry）；缺 key/失败返回 None。"""
    from app.core.registry import _instantiate

    if not config.llm.api_key:
        print("⚠️  未配置 LLM API Key，真实 LLM 不可用")
        return None
    try:
        return _instantiate("llm", config.llm.provider, config.llm.model_dump())
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  LLM 装配失败：{exc}")
        return None


def run_quality(cases: list[EvalCase], config, docs_dir: Path, kb_id: str,
                use_judge: bool = False, use_real_llm: bool = False,
                limit: int | None = None, fresh: bool = False) -> tuple[list[dict], dict]:
    """执行质量评测，返回 (逐案记录, 聚合指标 + 运行模式元信息)。

    - 默认用 Stub LLM 生成（只评检索/兜底，快且离线）。
    - use_real_llm=True 才用真实 LLM 生成；judge 与生成解耦（JUDGE=1 不会偷偷切真实生成）。
    """
    from app.cli.rageval import _run_in_process
    from app.eval.judge import LLMJudge

    if limit is not None:
        if limit <= 0:
            raise ValueError("limit 必须 > 0")
        cases = cases[:limit]
    # 只装配一次真实 LLM；按开关分别用于生成/judge，互不越界
    real_llm = _build_llm(config) if (use_judge or use_real_llm) else None
    gen_llm = real_llm if use_real_llm else None
    judge_llm = real_llm if use_judge else None
    generation_is_stub = gen_llm is None

    pairs = [{"id": c.id, "query": c.query, "type": c.type} for c in cases]
    responses = _run_in_process(config, docs_dir, pairs, kb_id=kb_id, fresh=fresh,
                                capture_contexts=True, llm=gen_llm)
    judge = LLMJudge(judge_llm) if (use_judge and judge_llm is not None) else None

    records: list[dict] = []
    judge_calls = 0
    judge_failures = 0

    def _record_judge(record: dict, key: str, result) -> None:
        """写入 judge 分数；失败（judge_error/parse）只记录原因，不伪装成 0 分。"""
        nonlocal judge_calls, judge_failures
        judge_calls += 1
        if result.reason.startswith("judge_"):
            judge_failures += 1
            record.setdefault("judge_errors", []).append(f"{key}:{result.reason}")
            return
        record[key] = result.score

    for case, response in zip(cases, responses):
        retrieved = _retrieved_ids(case, response)
        contexts = [str(c.get("text", "")) for c in (response.get("data", {}) or {}).get("contexts", [])]
        record = {
            "id": case.id, "type": case.type, "variant": case.variant,
            "source": case.source,
            "query": case.query, "code": response.get("code"),
            "answer": (response.get("data", {}) or {}).get("answer", ""),
            "retrieved": retrieved, "gold": _gold_ids(case),
            "expected_sources": list(case.expected_sources),
            "reference_answer": case.reference_answer,
            "expected_behavior": case.expected_behavior,
            "contexts": contexts, "elapsed_ms": response.get("elapsed_ms", 0),
        }
        if case.type in ("no_answer", "no_invent"):
            record["refusal_ok"] = _refusal_passed(case, response, generation_is_stub)
        # 拒答样本与正常回答的 Faithfulness 语义不同，分开统计（此处只评可答类）
        if judge is not None and case.type not in ("no_answer", "no_invent"):
            _record_judge(record, "judge_faithfulness",
                          judge.faithfulness(case.query, record["answer"], contexts))
            if case.reference_answer:
                _record_judge(record, "judge_correctness",
                              judge.answer_correctness(case.query, record["answer"], case.reference_answer))
            _record_judge(record, "judge_relevancy",
                          judge.answer_relevancy(case.query, record["answer"]))
        records.append(record)

    judge_mode = "off" if not use_judge else ("real" if judge_llm is not None else "unavailable")
    if judge_calls and judge_failures:
        judge_mode = "degraded" if judge_failures == judge_calls else "partial"
    meta = {
        "generation_mode": "stub" if generation_is_stub else "real",
        "generation_requested": "real" if use_real_llm else "stub",
        "judge_mode": judge_mode,
        "judge_requested": bool(use_judge),
        "judge_calls": judge_calls,
        "judge_failures": judge_failures,
        "dataset_sources": sorted({c.source for c in cases}),
        "limit": limit,
    }
    return records, {**aggregate_quality(records), **meta}


def aggregate_quality(records: list[dict]) -> dict:
    """聚合：IR（source/chunk 级）+ 兜底率 + judge 均值/CI。"""
    retrieval_items = [r for r in records if r["gold"] and r["type"] not in ("no_answer", "no_invent")]
    agg = {
        "total": len(records),
        "retrieval_n": len(retrieval_items),
        **aggregate_retrieval(retrieval_items, k=5, ndcg_k=10),
    }
    refusals = [bool(r.get("refusal_ok")) for r in records if "refusal_ok" in r]
    agg["refusal_rate"] = mean([1.0 if x else 0.0 for x in refusals])
    agg["refusal_n"] = len(refusals)
    for key in ("judge_faithfulness", "judge_correctness", "judge_relevancy"):
        values = [float(r[key]) for r in records if key in r]
        agg[key + "_n"] = len(values)
        if values:
            agg[key] = mean(values)
            agg[key + "_ci"] = bootstrap_ci(values)
    agg["elapsed_ms_avg"] = mean([float(r.get("elapsed_ms", 0)) for r in records])
    sources = sorted({str(r.get("source", "")) for r in records if r.get("source")})
    if sources:
        agg["dataset_sources"] = sources
    return agg


def render_report(agg: dict, records: list[dict], args) -> str:
    gen_mode = agg.get("generation_mode", "stub")
    judge_mode = agg.get("judge_mode", "off")
    requested = []
    if agg.get("generation_requested") == "real" and gen_mode != "real":
        requested.append("REAL 请求但真实 LLM 不可用，已回退 Stub")
    if agg.get("judge_requested") and judge_mode == "unavailable":
        requested.append("JUDGE 请求但真实 LLM 不可用，judge 已关闭")
    if gen_mode == "stub" and judge_mode == "real":
        requested.append("生成使用 Stub，judge 分数仅验证判分链路，不代表回答质量")
    if agg.get("judge_failures"):
        requested.append(
            f"judge 调用失败 {agg['judge_failures']}/{agg.get('judge_calls', 0)} 次，均值仅含成功调用")
    sources = agg.get("dataset_sources") or []
    is_smoke = sources == ["auto_converted"]
    intro = ("smoke 集为 `auto_converted`（非人工校准真值）" if is_smoke
             else f"数据集来源：{', '.join(sources) or '未知'}")
    lines = [
        "# 质量评测报告（P0-P2）", "",
        f"> 用途：链路 smoke 与回归观测。{intro}；",
        "> 不能作为质量结论或发布门禁；真正门禁需 D3 holdout + judge 人工校准。", "",
        f"- 用例集：{args.pairs}", f"- 文档目录：{args.docs}",
        f"- kb：{args.kb}", f"- 生成 LLM：{gen_mode}",
        f"- judge：{judge_mode}",
        f"- limit：{agg.get('limit') or '不限'}",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    lines += [f"- 注意：{msg}" for msg in requested]
    lines += ["", "## 聚合指标", "", "| 指标 | 值 | 样本数 |", "|---|---|---|"]
    rows = [
        ("Hit@5", agg.get("hit@5", 0.0)), ("Recall@5", agg.get("recall@5", 0.0)),
        ("Precision@5（单 gold 上限 20%）", agg.get("precision@5", 0.0)), ("MRR", agg.get("mrr", 0.0)),
        ("nDCG@10", agg.get("ndcg@10", 0.0)),
    ]
    for name, value in rows:
        lines.append(f"| {name} | {value:.2%} | {agg['retrieval_n']} |")
    if agg.get("refusal_n"):
        lines.append(f"| 兜底正确率 | {agg['refusal_rate']:.2%} | {agg['refusal_n']} |")
    else:
        lines.append("| 兜底正确率 | -（无样本） | 0 |")
    for key, name in (("judge_faithfulness", "Faithfulness"), ("judge_correctness", "AnswerCorrectness"),
                      ("judge_relevancy", "AnswerRelevancy")):
        n = agg.get(key + "_n", 0)
        if n and key in agg:
            lo, hi = agg.get(key + "_ci", (agg[key], agg[key]))
            lines.append(f"| {name} | {agg[key]:.2%}（95% CI {lo:.2%}~{hi:.2%}） | {n} |")
        else:
            lines.append(f"| {name} | -（未启用/无样本） | 0 |")
    saturation = "默认 smoke 仅 5 份 fixture、top_k=5，Hit@5/Recall@5 存在结构性饱和；" if is_smoke else ""
    lines += ["", f"> 注：{saturation}",
              "> 看检索质量优先看 MRR/nDCG 与后续 D3 holdout。", "",
              "## 逐条明细", "", "| ID | 类型 | 检索 | 兜底 | Faith | Correct | Relev | 耗时(ms) |",
              "|---|---|---|---|---|---|---|---|"]
    for r in records:
        refusal = "PASS" if r.get("refusal_ok") else ("FAIL" if "refusal_ok" in r else "-")
        lines.append(
            f"| {r['id']} | {r['type']} | {len(r['retrieved'])} 条 | {refusal} | "
            f"{r.get('judge_faithfulness', '-')} | {r.get('judge_correctness', '-')} | "
            f"{r.get('judge_relevancy', '-')} | {r.get('elapsed_ms', 0)} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 质量评测（P0-P2，设计稿见 docs/specs/测试与评测体系设计.md）")
    parser.add_argument("--pairs", required=True, help="统一 schema 的 jsonl/json 用例集")
    parser.add_argument("--docs", required=True, help="进程内入库的文档目录")
    parser.add_argument("--kb", default="quality_smoke",
                        help="目标知识库（默认 quality_smoke；不要用 default 跑质量 smoke）")
    parser.add_argument("--fresh-kb", action="store_true",
                        help="评测前清空目标库集合与幂等表，保证可复现（推荐）")
    parser.add_argument("--judge", action="store_true", help="启用 LLM judge（真实调用，需 API Key）")
    parser.add_argument("--real-llm", action="store_true", help="用真实 LLM 生成回答（默认 Stub；需 API Key）")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条（试点/成本控制，N>0）")
    parser.add_argument("--out", default=None, help="报告路径，默认 reports/eval-quality-{ts}.md")
    parser.add_argument("--export-jsonl", default=None, help="逐案原始结果（含上下文/分数）导出路径")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须 > 0")
    if args.kb == "default" and args.fresh_kb:
        parser.error("拒绝 --fresh-kb + --kb default：default 是生产默认库，不能清空")
    if args.kb == "default":
        print("⚠️  正在使用 default 知识库；质量 smoke 建议 --kb quality_smoke")

    from app.core.config import load_config

    cases = load_cases(args.pairs)
    errors = validate_cases(cases)
    if errors:
        print("⚠️  用例集校验告警：")
        for err in errors[:20]:
            print("  -", err)
        if len(errors) > 20:
            print(f"  ... 共 {len(errors)} 条")
    missing = sum(1 for c in cases if not c.reference_answer
                  and c.type in ("direct", "rewrite", "table", "multi_doc"))
    if missing:
        print(f"⚠️  {missing} 条有答案类用例缺少 reference_answer：仅计入 IR/兜底，不参与答案正确性。")

    config = load_config()
    records, agg = run_quality(cases, config, Path(args.docs), args.kb,
                               use_judge=args.judge, use_real_llm=args.real_llm,
                               limit=args.limit, fresh=args.fresh_kb)
    out = Path(args.out or f"reports/eval-quality-{time.strftime('%Y%m%d-%H%M%S')}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(agg, records, args), encoding="utf-8")
    if args.export_jsonl:
        export_path = Path(args.export_jsonl)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"报告已生成：{out}")
    print(f"用例 {agg['total']} 条，生成={agg.get('generation_mode')} judge={agg.get('judge_mode')}，"
          f"Hit@5={agg.get('hit@5', 0):.0%}，Recall@5={agg.get('recall@5', 0):.0%}，"
          f"兜底={agg['refusal_rate']:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
