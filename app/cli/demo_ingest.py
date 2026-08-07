"""demo-ingest：入库 demo 文档并检索验证（W2 验收：检索到正确片段）。

用法：make demo-ingest 或 python -m app.cli.demo_ingest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.core.config import DEFAULT_SETTINGS_PATH, load_config
from app.core.logging import get_logger, setup_logging

logger = get_logger("demo_ingest")

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_DIR = ROOT / "tests" / "eval" / "sample_data"
QA_PAIRS = ROOT / "tests" / "eval" / "qa_pairs.json"


def main() -> int:
    config = load_config(DEFAULT_SETTINGS_PATH)
    setup_logging(level=config.logging.level, json=config.logging.json_enabled)

    from app.core.registry import wire

    # 装配：加载 BGE 模型（首次联网下载）+ Chroma
    print("⏳ 装配组件（首次运行会自动下载嵌入模型，约几十~100MB）…")
    deps = wire(config)
    pipeline = deps.pipeline
    print(f"✅ 装配完成：embedding={config.embedding.model_name}（{deps.embedding.dimension} 维），"
          f"collection={config.vector_store.collection}")

    # 1) 入库 demo 文档
    files = sorted(p for p in SAMPLE_DIR.iterdir() if p.is_file())
    if not files:
        print(f"⚠️ 未找到 demo 文档：{SAMPLE_DIR}")
        return 1
    for path in files:
        job = pipeline.ingest(path.read_bytes(), path.name)
        print(f"[入库] {path.name:<14} job={job.job_id} status={job.status} "
              f"document_id={job.document_id} chunk_count={job.chunk_count}")
        if job.status != "success":
            print(f"   ⚠️ 入库失败 error_code={job.error_code}")

    # 2) 检索验证：direct/rewrite 类问题应命中期望来源文档
    pairs = json.loads(QA_PAIRS.read_text(encoding="utf-8"))["qa_pairs"]
    hits, total = 0, 0
    print("\n--- 检索验证（top5 来源）---")
    for pair in pairs:
        if pair["type"] not in ("direct", "rewrite"):
            continue
        total += 1
        results = deps.retriever.retrieve(pair["query"], k=5)
        top_sources = [r.chunk.metadata.get("source") for r in results]
        hit = any(s in top_sources for s in pair.get("expected_sources", []))
        hits += int(hit)
        mark = "✅" if hit else "❌"
        print(f"{mark} {pair['id']} {pair['query'][:22]:<24} top={top_sources[:3]}")
    print(f"\n结果：{hits}/{total} 命中期望来源文档（W2 验收判据 3）")
    return 0 if hits >= max(1, total - 2) else 1


if __name__ == "__main__":
    sys.exit(main())
