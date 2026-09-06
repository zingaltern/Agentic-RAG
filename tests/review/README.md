# 外部基准测试集（`make review`，DEVELOPMENT.md 8.4）

> 两层评测策略中的「**测试集**」：外部公开数据集，度量 RAG 真实效果（Hit@1/Hit@5、
> ContextPrecision/Recall、兜底率）。与自建「**验证集**」（`tests/eval/`，`make eval`）
> **物理分离**——自建集只做回归门禁，外部集看能力与趋势，互不混用。

## 数据集：CMRC 2018

- 来源：<https://github.com/ymcui/cmrc2018>（哈工大讯飞联合实验室；中文维基百科抽取式 QA，SQuAD 风格）
- 许可：CC BY-SA 4.0（仅用于本仓库内部评测，不随仓库分发原始数据）
- 原始文件：`raw/cmrc2018_dev.json`（约 3.4MB，dev 集 ~3200 题；已 gitignore，不入库）
- 下载/再生成：`python -m app.cli.build_review_set`（原始文件缺失时自动联网下载；
  固定 `--seed 42`，同参数再生成结果完全一致）

## 目录结构

```
tests/review/
├── raw/                              # 原始数据集（gitignore，可重新下载）
├── sample_data/cmrc/                 # 语料文档：5 个维基段落聚合 = 一份多主题 .txt（cmrc_NNN_首段标题.txt）
└── qa_pairs/cmrc/qa_pairs.jsonl      # 测试对（rageval 用例，独立 kb「cmrc」）
```

## 测试对（qa_pairs）结构——每条怎么测

| 字段 | 说明 |
|---|---|
| `id` | `E###`（direct）/ `N###`（no_answer） |
| `query` | 数据集原始问题（不做人工改写，保持外部真实分布） |
| `type` | `direct` / `no_answer`，判据同 8.3（单一实现） |
| `expected_sources` | 该问题所属文章生成的语料文档文件名（仅 direct 有） |
| `variant` | `wiki`（direct）/ `oov`（no_answer），报告特征分布用 |

- **direct（E 系列）**：问题来自某篇已入库文章，答案可在该文章语料中抽取得到 →
  系统把对应文档检入 Top5（Hit@5）即 PASS；同时产出 Hit@1 / C-Prec / C-Rec。
- **no_answer（N 系列）**：问题取自**未入库**的文章（域外追问），且其问题与答案文本
  均不出现在任何语料中 → 期望系统诚实兜底（返回 `21001` 或"未找到相关依据"类文案），
  测的是"**不编造**"。
- 抽样约束：段落 200~4000 字（贴近真实文档片段）；每 5 个段落聚合为一份多主题文档
  （避免一题一文使检索退化为平凡任务）；每段落 ≤1 题；问题跨集去重；
  语料文档在 `--fresh-kb` 下每轮全新入库，结果可复现。

## 运行

```bash
make review                                      # 全新入库评测 → reports/review-{ts}.md
make review BASELINE=reports/review-<基线>.md     # 与基线对比（报告头部出 Δ 列）
python -m app.cli.build_review_set --num-docs 50 # 自定义规模再生成（默认 30 篇 / 每篇 ≤2 题 / 10 条域外）
```

首轮运行的报告即基线；外部集指标**只看趋势（Δ）不看单点**，不设"全绿"硬门禁（如实记录低分项）。
