# tests/eval — 开发评测集与评测工具（197 条 = 自建 107 + complex 难度分层 90，资产回归）

> 本项目**开发侧**回归评测集：与功能演进同步增补，涉及检索/改写/检测/兜底等影响指标的功能改动
> 必须按 `DEVELOPMENT.md` 13.1 度量协议跑本集并留前后对比报告。
> **独立冻结基准**（不随开发周期增删）另见 `tests/eval_suite/`（120 条，第二道门禁）。

## 目录结构

```
tests/eval/
├── README.md           # 本文件
├── qa_pairs/           # 评测 QA 集（jsonl，按库归档）
│   ├── default/        #   21 条（配套 sample_data/ 根目录文档）
│   ├── docs/           #   86 条（配套 sample_data/docs/ 文档）
│   └── complex/        #   90 条（难度分层，8.5.1，配套 sample_data/complex/）
├── rag_eval.py         # 【遗留】W1~W4 的最小评测脚本（早期单文件格式已迁移 jsonl 分库；qa_pairs.json 已删除）
└── sample_data/        # 配套 Demo 文档
    ├── 员工手册.md / IT支持手册.md / 产品FAQ.txt   # default 库
    ├── docs/           # 差旅政策.md / 报销制度.md   # docs 库
    └── complex/        # 多格式复杂语料（43 份），由 build_eval_stress 生成
```

## 1. 如何运行评测

**进程内模式（默认，不赖外网/真 key）**：真实 BGE + Chroma，LLM 用 Stub。

```bash
make eval                       # 等价于 --all-kbs --in-process，报告 reports/eval-*.md
.venv/bin/python -m app.cli.rageval --kb default --in-process            # 单库
.venv/bin/python -m app.cli.rageval --all-kbs --in-process --baseline <基线报告>  # 全库+基线对比
```

**HTTP 真实链路（真 DeepSeek）**：先 `make run`，再：

```bash
.venv/bin/python -m app.cli.rageval --all-kbs --base-url http://127.0.0.1:8000 --baseline reports/<最新>
# 认证启用时追加 --login <用户名> <密码> 或 --token <token>
```

- 报告默认输出 `reports/eval-{时间戳}.md`（`--out` 可指定）；`reports/` 已 gitignore，产物不入库。
- `--in-process` 模式走 `orchestration.run` 同一编排链路（13.11.2 口径统一），禁止两套实现。

## 1.1 complex 难度分层验证集（8.5.1）

- 由 `python -m app.cli.build_eval_stress` 生成（静态内容，可复现；单测含"回环自检"：
  全部语料经项目解析器真解析、事实句逐字可抽出、质量门零触发）。
- 语料 43 份覆盖 8 种格式：多页 PDF（画线表格）/ DOCX（真表格）/ XLSX / PPTX / HTML /
  EML 邮件线程 / ≥3000 字长文 / 噪声 TXT（错别字+OCR 空格+广告行）/ 近重复与完全重复对。
- 测试对 90 条按 `variant` 分 9 层（direct 24 / paraphrase 12 / multi_doc 10 / table 10 /
  noisy 8 / long_doc 6 / cross_fmt 6 / oov 8 / no_invent 6），报告"特征分布"逐层出通过率。
- 判读纪律：default/docs 库要求全绿；**complex 库看逐层通过率与 Δ**，首轮基线存档后不回退即可。

## 2. 判据与指标（对应 DEVELOPMENT.md 8.3 / 13.6）

| 项 | 规则 |
|---|---|
| no_answer 判据 | `pass =（code=0 且 answer 含"未找到相关依据"类文案）∨（code=21001）`（rageval.py 单一常量） |
| 指标 | Hit@1 / Hit@5、Faithfulness（分类型）、AnswerRelevancy、按库汇总、`--baseline` 时输出 diff 列 |
| 类型 | direct / rewrite / no_answer / no_invent / 口语 / 表格 / 多文档（`type` 字段） |

`expected_sources` 必须与入库文档的 `metadata.source`（即文件名）**逐字一致**（含扩展名、大小写）；
进程内模式 `--docs` 缺省按 `--kb` 自动取 `sample_data` 对应用档目录。

## 3. 怎么换成真实的业务数据集

1. 上传真实文档，观察文档列表 `metadata.source` 值（就是文件名）；
2. 在 `qa_pairs/<kb>/qa_pairs.jsonl` 按行追加用例，字段契约：
   ```json
   { "id": "Q01", "type": "direct", "query": "……",
     "expected_sources": ["<上传时的文件名>"], "golden_answer": "……",
     "expected_behavior": "answer_found" }
   ```
   `expected_behavior`：`answer_found` | `fallback_21001`（no_answer 类）| 防幻觉语义（no_invent 类）；
3. 建议配比：direct ≥ 60%、rewrite 15%、no_answer 15%、no_invent 10%；
4. 新问题优先用真实用户提问语料（工单、客服记录）收集，别自己编题。

## 4. 注意

- `sample_data/` 属配套 Demo 文档；接入真实数据后建议删除或挪出仓库，避免误传为正式知识库。
- rewrite 类不做"改写是否生效"的因果判定，请人工抽查 `/api/query` 的 `debug:true` 反思轨迹。
- 历史遗留 `rag_eval.py` 仅存档 W1~W4 用法（旧 `qa_pairs.json` 已删除），新评测一律走 `app.cli.rageval`。