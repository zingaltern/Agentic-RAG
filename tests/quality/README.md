# tests/quality —— 质量评测 smoke（P0-P2）

> 定位：把开发集自动转成**统一 schema** 的 smoke 质量集，跑通“进程内检索/问答 → IR 指标 →
> 可选 LLM judge → 报告”的链路。**不是人工校准的发布门禁**；真正质量门禁需按
> `docs/specs/测试与评测体系设计.md` 的 D3 holdout（真实问句 + 双人标注 + judge 校准）建设。

## 目录

```
tests/quality/
├── README.md
├── fixtures/                       # 冻结的 smoke 语料（从 tests/eval/sample_data 复制）
├── datasets/smoke_quality.jsonl    # 30 条统一 schema 用例（default 21 + docs 9）
└── (后续) holdout/                 # D3 发布集（需人工标注，落地后加入）
```

## 运行

```bash
make eval-quality                                  # IR 指标（Stub 生成，不调外网；fresh 重建）
make eval-quality JUDGE=1 LIMIT=5                  # 仅 judge（生成仍是 Stub，需 RAG_DEEPSEEK_API_KEY）
make eval-quality REAL=1 JUDGE=1 LIMIT=5           # 真实 LLM 生成 + judge（同族自评偏差）
python -m app.cli.eval_quality --pairs tests/quality/datasets/smoke_quality.jsonl \
    --docs tests/quality/fixtures --kb quality_smoke --fresh-kb --real-llm --judge --limit 5
```

- `JUDGE=1` **只**启用 judge，不会把生成切换为真实 LLM；`REAL=1` 才用真实 LLM 生成。
- `make eval-quality` 默认 `--fresh-kb`；CLI 默认 kb 为 `quality_smoke`（不要用 `default`）。
- 本 smoke 是**单库**运行：case 的 `kb_id` 仅作标注，不按 case 分库。

- 报告：`reports/eval-quality-{ts}.md`；`--export-jsonl` 可导出逐案原始结果（含完整上下文与分数）。
- 指标实现：`app/eval/metrics.py`（IR/claim/引用/兜底）；judge：`app/eval/judge.py`。
- schema：`app/eval/schema.py`；旧字段 `golden`/`golden_answer`/`expected_doc` 自动兼容。

## 数据说明（重要）

- `smoke_quality.jsonl` 由 `tests/eval/qa_pairs/{default,docs}` **自动转换**：
  `reference_answer` 来自原 `golden_answer`；`reference_evidence` 为按数字/字符重合自动抽取的句子。
- 自动抽取**未经人工复核**，`source=auto_converted`；只能做链路 smoke，不能作为质量结论。
- 证据为文件名级（fixtures 未预置 chunk_id），因此 IR 指标当前是 **source 级**；chunk 级指标
  需要 D3 holdout 的 `reference_evidence.chunk_id`。`Precision@5` 按标准 /5 计算，单 gold 用例
  上限为 20%，看检索质量优先用 Hit@5 / Recall@5 / MRR / nDCG@10。
- D3 holdout 建设标准见设计稿第 5 章（真实问句、双人标注、κ≥0.7、冻结哈希）。
- `REAL=1 JUDGE=1` 试点时生成与 judge 复用同一 LLM（同族自评偏差），只作链路验证，不作质量结论；
  2026-10-20 30 条 smoke 实测（judge 仅统计 25 条可答类）：Faithfulness 99.0% /
  AnswerCorrectness 98.0% / AnswerRelevancy 93.2%（用例简单且饱和，读数偏乐观）。
