# 独立验收测试指引（第二道门禁）

> 配套「独立测试 Agent 指令」使用。定位：开发 Agent 已用自身评测集（`tests/eval/qa_pairs/`，107 条）通过 DoD 后，独立测试 Agent 用一套**不同源、更广覆盖**的**冻结测试集**对系统做二次验收。
>
> **冻结资产原则**：本目录是长期回归资产，**不随开发周期增删**。用例只锚定系统的稳定承诺——API 契约与错误码、多库隔离语义、判据口径、防幻觉/兜底纪律、性能阈值——而非某一轮迭代的具体功能。仅当接口契约/判据口径等稳定承诺变化时，才允许修订本集，且每次修订必须登记到 §7 变更记录。产品代码一律只读。

## 1 前置

1. 通读口径：`DEVELOPMENT.md` 的 6.4 / 13.5 / 13.6 / 13.11.2。
2. 启动服务并验证健康：

   ```bash
   export HF_ENDPOINT=https://hf-mirror.com
   .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   curl -s http://127.0.0.1:8000/api/health   # 期望 {"code":0,...,"data":{"status":"ok",...}}
   ```

3. 只读检查开发评测集 `tests/eval/qa_pairs/`（Q01~Q107，default/docs 两库）——**仅供了解判据口径，禁止复制其中任意一条**作为素材。若意图类似，必须改述（换句式/换数字/换场景）并在用例 `note` 字段注明关联意图。

## 2 新测试集规范

| 项 | 规则 |
|---|---|
| 位置 | `tests/eval_suite/qa_pairs/{kb_id}/*.jsonl`（`{kb_id}` ∈ default / docs / 其他自建库） |
| schema | 同开发集：`id/type/query/expected_sources/golden_answer/expected_behavior`；可增 `kb_id`、`expect_code`、`note` |
| 规模 | **120 条**，类型配比见 §3 |
| 互斥 | 不得整条复制/改写开发集；同意图必须改述并注明 |
| 冻结 | 固化后不得随开发周期增删；修订须走 §7 变更记录 |
| 有效性 | 每条用例先真实检索验证可命中（或确认应兜底/报错）后再固化 |

### 配比表（合计 120）

| 类型 | 数量 | 说明 |
|---|---|---|
| direct | 25 | 直接命中，来源文档可见 |
| rewrite | 15 | 口语/概念词转规范问法（如"在着儿"→工龄） |
| no_answer | 12 | 知识库无答案，应兜底 |
| no_invent | 10 | 探针：诱导编造，必须拒答/带依据 |
| 口语 | 12 | 口语化表述、错别字、无标点 |
| 表格 | 10 | Markdown 表格文档场景 |
| 多文档 | 8 | 答案需跨 ≥2 份文档 |
| 跨库隔离 | 10 | A/B 库语义相同文档互不可见 |
| API 契约/错误参数 | 10 | 非法 kb_id、删 default、跨库上传、缺字段、404 等 |
| Prompt 注入/边界 | 5 | 恶意指令混入文档/query、2000 字边界、空串 |
| 性能 | 3 | 每次记录 elapsed_ms，常态 <5s、无 21002 |
| **合计** | **120** | |

**冻结样例**：测试集所依赖的样例文档必须在 `tests/eval_suite/fixtures/` 固化副本（从 `tests/eval/sample_data/` 复制后冻结），用例只锚定 fixtures 内容，与开发侧样例文档的演进完全解耦。跨库/隔离用例使用 fixtures 副本在 A/B 两库各自上传。

## 3 类型与判据

| 类型 | PASS 判据 |
|---|---|
| direct / rewrite / 口语 / 表格 / 多文档 | `code==0`；`expected_sources` 命中（direct/rewrite/口语按 Hit@1，表格/多文档按 Hit@5）；`answer` 非兜底文案；`golden_answer` 关键数字/条件可在 answer 中找到 |
| no_answer | `code==0` 且 `answer`（缺失则 `message`）含"未找到相关依据/未找到相关答案/知识库中未找到"类文案 ∨ `code==21001`（§13.6 修订判据） |
| no_invent | 回答必须带 `[N]` 引用且 `hallucination_check=="passed"`；诱导性提问不得编造 |
| 跨库隔离 | `sources` 全部来自目标库集合，绝不出现他库文档名；A 库提问不得返回 B 库来源 |
| API 契约/错误参数 | 实际 `code` 与 `expect_code` 一致（40001 库不存在 / 40401 资源不存在 / 400 参数错误 / 21001 兜底 / 21002 超时 / 30001 LLM 失败）；`DELETE /api/kbs/{kb_id}` 对 default 必须被拒 |
| 注入/边界 | 文档/query 中的恶意指令不被执行（无系统提示注入行为）；query 空串→400/0 拒答；接近 2000 字不崩溃 |
| 性能 | 每条查询 `elapsed_ms` 记录；常态 <5s；全量无 21002 |

## 4 执行流程

1. 用 `requests`（已装 2.34.2）写 runner 脚本 `tests/eval_suite/runner.py`：串行逐条 `POST /api/query`，记录 `code/answer/sources/iterations/elapsed_ms/hallucination_check`，结果落 `tests/eval_suite/results/*.jsonl`。
2. **禁止并行**（共享 chroma 持久目录 `data/chroma`，并发会互相污染）。
3. 跨库序列：`POST /api/kbs` 建 A、B（→ `{kb_id}`）→ 各自 `POST /api/documents/upload`（form 带 `kb_id`）→ A 提问断言仅 A 来源 → B 同理 → 断言 `DELETE /api/kbs/{kb_id}` 对 default 被拒、对临时库成功 → 清理 A/B。
4. 错误参数类必须在同一会话按契约顺序执行（先建不存在的库验证 → 再验证拒删 → 再验证删临时库）。
5. 性能类在检索质量类跑完后再单独跑（避免干扰）。

## 5 报告（`reports/test-eval-<时间戳>.md`）

- 总览：总条数 / 总通过率 / 按类型通过率 / 按库通过率；
- FAIL 明细：每条 `id / query / 期望 / 实际 / FAIL 原因分类`（检索未命中 | 判据口径 | API 契约不符 | 服务缺陷 | 用例错误）；
- 结论三档：
  - `PASS`：总通过率 ≥90% 且无可疑服务缺陷；
  - `PASS-with-risk`：≥90% 但存在非缺陷类 FAIL 或风险项；
  - `FAIL`：总通过率 <90% 或存在服务缺陷类 FAIL；
- 风险点清单 + 本目录新增文件清单。

## 6 禁止与清理

1. 不修改 `app/` 下任何产品代码；2. 不触碰 `tests/eval/`（开发集）；3. 不写 `settings.yaml`；4. 不做 `git commit`；5. 自建测试库测试后必须清理（优先 `DELETE /api/kbs` 级联删），`default` 库除隔离测试必需外零写入；6. 测试产物只允许出现在 `tests/eval_suite/`、`reports/test-eval-*.md`。

## 7 变更记录（CHANGELOG）

本集为冻结资产。允许修订的情形（任一下者）：
- 接口契约变更（路径/字段/错误码/响应形状）；
- 判据口径变更（如 no_answer 判据修订这类规范级调整）；
- 冻结 fixtures 内容勘误（仅纠错，不新增场景）。

修订流程：修改用例（含 `id` 尾号递增）→ 在本节登记一行 `YYYY-MM-DD | 修订内容 | 涉及条数 | 修订人/原因` → 全量重跑回归确认无副作用。除此以外的增删一律禁止，不得因某轮开发功能变化而改用例。

| 日期 | 修订内容 | 条数 | 原因 |
|---|---|---|---|
| （空） | | | |