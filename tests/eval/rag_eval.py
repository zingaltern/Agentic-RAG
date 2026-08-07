"""最小评测脚本：调用 POST /api/query 对 qa_pairs.json 逐条评测并输出 Markdown 报告。

用法（API 启动后）：
    python tests/eval/rag_eval.py --pairs tests/eval/qa_pairs.json \
        --base-url http://localhost:8000 --out reports/eval-demo.md

仅使用标准库（urllib），不引入第三方依赖。配合 tests/eval/README.md 使用。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CODE_FALLBACK = 21001  # 兜底：未找到可支撑答案（DEVELOPMENT.md 6.7 错误码表）
CODE_TIMEOUT = 21002


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
    with urllib.request.urlopen(req, timeout=40) as resp:
        return json.loads(resp.read().decode("utf-8"))


def eval_pair(pair: dict, base_url: str) -> dict:
    q = pair["query"]
    kind = pair["type"]
    expected = set(pair.get("expected_sources", []))
    started = time.time()
    try:
        body = call_query(base_url, q)
    except urllib.error.HTTPError as e:
        return {"id": pair["id"], "kind": kind, "ok": False,
                "error": f"HTTP {e.code}", "elapsed_ms": 0, "detail": ""}
    except Exception as e:  # noqa: BLE001
        return {"id": pair["id"], "kind": kind, "ok": False,
                "error": f"{type(e).__name__}: {e}", "elapsed_ms": 0, "detail": ""}

    code = body.get("code")
    data = body.get("data", {}) or {}
    elapsed = round((time.time() - started) * 1000)
    sources = [s.get("filename", "") for s in data.get("sources", [])]

    if kind in ("direct", "rewrite"):
        ok = bool(expected) and expected.issubset(sources)
        return {"id": pair["id"], "kind": kind, "ok": ok,
                "error": "" if ok else f"期望来源{expected} 未命中(实际{sources[:5]})",
                "elapsed_ms": elapsed,
                "detail": f"iterations={data.get('iterations')} check={data.get('hallucination_check')}"}
    if kind == "no_answer":
        ok = code == CODE_FALLBACK
        return {"id": pair["id"], "kind": kind, "ok": ok,
                "error": "" if ok else f"期望兜底({CODE_FALLBACK})，实际 code={code}",
                "elapsed_ms": elapsed, "detail": data.get("answer", "")[:60]}
    if kind == "no_invent":
        ok = (code == CODE_FALLBACK) or (data.get("hallucination_check") != "passed") or \
             ("未" in (data.get("answer", "")[:80]) or "没有" in (data.get("answer", "")[:80]))
        return {"id": pair["id"], "kind": kind, "ok": ok,
                "error": "" if ok else "疑似编造（回答未提示无依据）",
                "elapsed_ms": elapsed, "detail": data.get("answer", "")[:60]}
    return {"id": pair["id"], "kind": kind, "ok": False, "error": f"未知 type: {kind}",
            "elapsed_ms": elapsed, "detail": ""}


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG QA 评测")
    parser.add_argument("--pairs", default="tests/eval/qa_pairs.json")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--out", default="reports/eval.md")
    args = parser.parse_args()

    pairs = load_pairs(Path(args.pairs))
    results = [eval_pair(p, args.base_url) for p in pairs]

    kinds = {}
    for r in results:
        kinds.setdefault(r["kind"], {"total": 0, "ok": 0})
        kinds[r["kind"]]["total"] += 1
        kinds[r["kind"]]["ok"] += 1 if r["ok"] else 0

    lines = [
        "# RAG 评测报告",
        "",
        f"- 数据：`{args.pairs}`（{len(pairs)} 条）",
        f"- 服务：`{args.base_url}`",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 汇总",
        "",
        "| 类型 | 总数 | 通过 | 通过率 |",
        "|---|---|---|---|",
    ]
    total_ok = sum(1 for r in results if r["ok"])
    for k in sorted(kinds):
        d = kinds[k]
        lines.append(f"| {k} | {d['total']} | {d['ok']} | {d['ok'] / d['total']:.0%} |")
    lines.append(f"| **合计** | {len(results)} | {total_ok} | {total_ok / len(results):.0%} |")
    lines += ["", "## 明细", "", "| ID | 类型 | 结果 | 耗时(ms) | 备注 | 说明 |", "|---|---|---|---|---|---|"]
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        lines.append(f"| {r['id']} | {r['kind']} | {mark} | {r['elapsed_ms']} | {r['detail']} | {r['error']} |")
    lines.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已生成：{out}  通过率 {total_ok}/{len(results)}")


if __name__ == "__main__":
    main()
