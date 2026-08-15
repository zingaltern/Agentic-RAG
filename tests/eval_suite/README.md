# 独立基准测试指引（第二道门禁）

> 配套「独立测试 Agent 指令」使用。定位：开发 Agent 已用自身评测集（`tests/eval/qa_pairs/`，107 条）完成功能通过后，独立测试 Agent 用一套**不同源、更广覆盖、冻结**的基准集（**120 条**）评测系统的**可用性**——核心是两个维度：**准确率**与**耗时**，输出评级报告，回答"这套 RAG 好不好用"。
>
> **冻结资产原则**：本目录是长期回归基准，**不随开发周期增删**。用例只锚定系统的稳定承诺——检索准确、答案可信、兜底/隔离/契约正确、延迟可控——而非某一轮迭代的具体功能。仅当接口契约或判据口径变化时才允许修订，每次修订登记到 §7 变更记录。产品代码一律只读。

## 1 前置

1. 通读口径：`DEVELOPMENT.md` 的 6.4 / 13.5 / 13.6 / 13.11.2。
2. 启动服务并验证健康：

   ```bash
   export HF_ENDPOINT=https://hf-mirror.com
   .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   curl -s http://127.0.0.1:8000/api/health   # 期望 {"code":0,...,"data":{"status":"ok",...}}
   ```

3. 只读检查开发评测集 `tests/eval/qa_pairs/`（107 条）——仅了解口径，**禁止复制任何一条**；同意图必须改述并在 `note` 注明。
4. 数据来源说明：`POST /api/query` 响应 `data` 含 `answer / sources / hallucination_check / iterations / elapsed_ms`，本指引全部指标基于这些字段计算。

## 2 基准集规范

| 项 | 规则 |
|---|---|
| 位置 | `tests/eval_suite/qa_pairs/{kb_id}/*.jsonl`（`{kb_id}` ∈ default / docs / 其他自建库） |
| schema | `id / type / query / expected_sources / golden_answer / keywords`（golden 中必须覆盖的数字/条款关键词数组，用于回答准确率）；可增 `expect_code`、`note` |
| 规模 | **120 条**，配比见下表 |
| 互斥 | 不得整条复制/改写开发集；同意图必须改述并注明 |
| 冻结 | 固化后不随开发周期增删；修订走 §7 |
| 有效性 | 每条先真实检索验证可命中（或确认应兜底/报错）再固化 |

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
| API 契约/错误参数 | 10 | 非法 kb_id、删 default、跨库上传、缺字段等，断言 `expect_code` |
| Prompt 注入/边界 | 5 | 恶意指令混入、2000 字边界、空串 |
| 性能加压 | 3 | 超长上下文/高并发单条场景，纳入耗时分布 |
| **合计** | **120** | |

**冻结样例**：依赖的样例文档复制到 `tests/eval_suite/fixtures/` 并冻结（从 `tests/eval/sample_data/` 拷入），用例只锚定 fixtures 内容，与开发侧样例演进解耦。跨库用例用 fixtures 副本在 A/B 两库各传一份。

## 3 评测维度与指标

| 维度 | 指标 | 计算方式 |
|---|---|---|
| 检索准确率 | Hit@1 / Hit@5 | 期望来源是否出现在 `sources` 前 1 / 前 5 位 |
| 回答准确率 | `answer_key_hit_rate` | `keywords` 全部命中 answer 的用例 / 有效回答用例（direct/rewrite/口语/表格/多文档） |
| 引用纪律 | `faithful_rate` | 上述类型中 `hallucination_check == "passed"` 的比例 |
| 兜底正确率 | `fallback_correct_rate` | no_answer/no_invent 类：按已改判据判定正确的比例（code0+未找到文案∨21001；no_invent 须带 `[N]`） |
| 隔离正确率 | `isolation_leak` | 跨库用例中出现他库 sources 的条数（期望 0 泄漏） |
| 契约正确率 | `contract_rate` | `expect_code` 与实际 `code` 一致的用例/契约类用例 |
| 耗时 | `avg / P50 / P90 / P95 / max`（ms） | 全部 120 条的 `elapsed_ms` 分布；**前 3 条为预热查询不计入**（首次含模型加载）；另按类型分列均值 |
| 迭代开销 | `avg_iterations` | 每条 `iterations` 均值（反思循环轮次） |
| 超时风险 | `timeout_count` | `data.code == 21002` 的条数（期望 0） |

## 4 可用性评级（结论口径：好用/不好用，而非通过/不通过）

对每个维度按达标线判定，输出评级：

| # | 维度 | 达标线 |
|---|---|---|
| R1 | 检索准确率 Hit@1 | ≥ 85% |
| R2 | 回答准确率 answer_key_hit_rate | ≥ 80% |
| R3 | 引用纪律 faithful_rate | ≥ 95% |
| R4 | 兜底正确率 fallback_rate | ≥ 95% |
| R5 | 隔离泄漏 | = 0 |
| R6 | 契约正确率 | = 100% |
| R7 | 耗时 P50 | < 2000ms |
| R8 | 耗时 P95 | < 5000ms |
| R9 | 超时 count(21002) | = 0 |

综合评级：
- **A 好用**：R1~R9 全部达标；
- **B 基本可用**：≤2 项未达标（报告须列明短板维度与差距）；
- **C 不可用/需优化**：≥3 项未达标（或隔离泄漏 > 0 / 契约类全部不符）。

## 5 执行流程

1. 用 `requests`（2.34.2 已装）写 `tests/eval_suite/runner.py`：串行逐条 POST `/api/query`，记录 `code/data/sources/elapsed_ms/iterations/hallucination_check`；结果落 `tests/eval_suite/results/*.jsonl`。**禁止并行**（共享 chroma）。
2. 顺序：契约/错误参数类 → 检索质量类 → 兜底/防幻觉类 → 隔离类 → 注入/边界 → 性能加压类（每类间留 1s 间隔，避免排队干扰耗时统计）。
3. 跨库序列：`POST /api/kbs` 建 A、B → 各自上传 fixtures → A 提问断言 sources 无 B → B 同理 → 验证删 `default` 被拒 → DELETE A/B 清理。
4. 预热：正式统计前先发 **3 条 dummy 查询**（不计入），跑两次取第二次作为耗时样本。
5. 指标计算：严格按 §3 公式；`keywords` 匹配与 golden 文档内容逐字核对。

## 6 报告（`reports/benchmark-<时间戳>.md`）

- 总览：条数、预热说明、版本（git 当前提交+工作区状态）；
- 指标表：§3 每维度实际值 vs 达标线（含 P50/P90/P95/max 及按类型耗时均值）；
- 评级结论：A / B / C + 依据；
- 维度短板明细：R1~R9 未达标项各自差距；
- 功能性异常清单：契约不符、隔离泄漏、服务错误（分类：检索未命中 | 回答质量差 | API 契约不符 | 服务缺陷 | 用例错误）；
- 风险点清单 + 本目录新增文件清单。

## 7 禁止与变更记录

1. 不修改 `app/` 下任何产品代码；2. 不触碰 `tests/eval/`（开发集）；3. 不写 `settings.yaml`；4. 不做 `git commit`；5. 自建测试库用后必清理，`default` 库除隔离必需外零写入；6. 测试产物只出现在 `tests/eval_suite/`、`reports/benchmark-*.md`。

**变更记录**：基准集为冻结资产，仅接口契约/判据口径变化或 fixtures 勘误时可修订，登记如下：

| 日期 | 修订内容 | 涉及条数 | 原因 |
|---|---|---|---|
| 2026-08-15 | 基准集初始化并固化：default 38 / docs 54 / contract 10 / bench 18 = 120 条；fixtures 4 份（员工手册/IT支持手册/差旅政策/报销制度） | 120 | 首版冻结资产（配比符合 §2 表） |