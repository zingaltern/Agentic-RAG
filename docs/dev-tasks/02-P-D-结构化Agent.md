# 开发指令 02：P-D 真 Agentic（结构化 Agent）

> 依据：DEVELOPMENT.md 15.1（当前定位）、15.4 演进路线 P-D、15.5 决策记录（方向=结构化 Agent）。
> 性质：**单独立项的大改造**。涉及接口契约扩展 + 编排重写 + 评估基线重跑，禁止一次改完直接交付。

## 背景（当前现状，已核实）

- 当前为 Iterative/Self-Correcting RAG：`app/services/orchestration.py` `run()`（L195）是固定 `while True` 循环——多路检索 → 精排 → 评估 →（passed 则回溯+生成 / 未通过则改写或换路再检索），无规划器、无工具调用。
- 现有组件均可作为"工具"复用：`retriever.retrieve`（hybrid/bm25/vector 三路）、`reranker.rerank`、`evaluator.evaluate`、`rewriter.rewrite`、`detector.detect`、`llm.chat`。
- 接口契约：`app/core/interfaces.py`（VectorEngine/VectorStore/Retriever/Reranker/Evaluator/QueryRewriter/HallucinationDetector/LLM/Cleaner），契约测试 `tests/unit/test_contracts.py`。
- 评估体系：`make eval`（`app/cli/rageval.py`，进程内）+ `tests/eval/qa_pairs/{default,docs}/` 107 条；基线指标见 DEVELOPMENT.md 14.3。
- 编排配置：`configs/settings.yaml` `orchestration:` 段（max_reflection_iterations=3、search_timeout_seconds=10、request_timeout_seconds=30、max_concurrent_queries=4）。

## 任务目标

引入**结构化 Agent**：LLM 通过"工具调用协议"自主决策执行步骤（检索/精排/总结/结束），取代当前硬编码循环。要求：

1. **工具注册表**：统一注册可用工具（retrieve/bm25/vector/rerank/rewrite/answer），每工具带 schema（名称/参数/说明）。
2. **规划器**：LLM 根据 query + 工具列表输出下一步动作（ReAct 式 thought/action/observation 循环，但以结构化 JSON 表达）。
3. **循环执行**：编排层解释动作 → 执行工具 → 反馈 observation → 继续，直到 LLM 输出 answer 或达到 max_steps。
4. **兜底保持**：LLM 工具调用失败/超时/格式错误时，回退到现有"检索→评估→改写"流程（当前行为不得消失）。

## 实现要求

### 阶段 A：契约与协议（先行落地，可独立交付）

1. `app/core/interfaces.py` 新增：
   - `Tool`（`name`, `description`, `parameters: dict[str, Any]`, `invoke(**kwargs) -> Any`）。
   - `Planner`（`plan(query, tools, history, feedback) -> PlanStep`，`PlanStep` 含 `{action: str, args: dict, thought: str}`）。
2. 现有组件包装为 Tool 实例（放 `app/adapters/tools/`，如 `retrieve_tool.py`、`rerank_tool.py`、`answer_tool.py`）——**不改原组件**，只做薄封装。
3. 契约测试补：Tool/Planner 签名断言。
4. LLM 工具调用协议：定义消息格式（system 提示含工具 schema JSON；模型输出 JSON 动作；不强制用 function-calling API，用文本 JSON 解析 + 重试）。

### 阶段 B：编排集成（兼容双模式）

5. `configs/settings.yaml` `orchestration:` 新增 `mode: "reflect"`（默认，保持现行为）| `"agentic"`（新循环）。`agentic_max_steps: 8`。
6. `orchestration.run()` 按 mode 分发：
   - `reflect`：现有逻辑不变（零回归）。
   - `agentic`：规划器循环；每步记录 `debug_trace`；时间预算检查沿用 `_check_budget`。
7. 回退规则：LLM 解析失败连续 N 次（如 2）→ 自动切回 `reflect` 路径。
8. `/api/query` 透传 mode（请求体可选字段，默认走配置），前端问答控制台加"Agentic 模式"开关（`QueryConsole.vue`，checkbox 与"展示反思过程"并列）。

### 阶段 C：评估与验收

9. **必做**：`make eval` 全库跑一遍，与 14.3 基线对比（Hit@1/Hit@5/Faithfulness/兜底率）。agentic 模式任一指标低于基线 5% → 不合并，需调优（工具提示词/检索参数）。
10. 单测：`tests/unit/test_tools.py`（工具封装）、`tests/unit/test_planner.py`（规划器解析/失败重试）、`test_orchestration_agentic.py`（agentic 循环 + 回退，Stub LLM 输出确定性 JSON）。
11. 全量 `make test` 全绿（基线 327 项），更新 AGENTS.md 计数。

## 验收标准

1. `mode: reflect`（默认）行为与现在完全一致，`make eval` 无回退。
2. `mode: agentic` 下，含明确检索意图的问题能自主走"检索→总结"多步并给出带引用的答案。
3. LLM 输出非法 JSON / 超时 → 自动回退 reflect，不报错中断。
4. `debug_trace` 完整记录每步 thought/action/observation，前端可展示。
5. 单测 + 全量回归 + eval 对比报告（`reports/eval-*.md`）齐备。

## 关键代码入口

| 环节 | 文件 | 说明 |
|---|---|---|
| 编排 | `app/services/orchestration.py` `run()`（L195-320） | mode 分发 + agentic 循环 |
| 接口契约 | `app/core/interfaces.py` | 新增 Tool/Planner |
| 工具封装 | `app/adapters/tools/`（新建） | 现有组件薄封装 |
| 评估 | `app/cli/rageval.py` + `tests/eval/qa_pairs/` | 基线对比（107 条） |
| 前端 | `frontend/src/views/QueryConsole.vue` | Agentic 模式开关 |
| 配置 | `configs/settings.yaml` `orchestration:` | mode / agentic_max_steps |

## 注意事项

- 这是**分期改造**：阶段 A 可独立交付验收；阶段 B 后必须跑 eval；阶段 C 不达标不合并。
- 禁止删除或绕过现有 reflect 路径（兜底依赖它）。
- 新增依赖需在 `pyproject.toml` 登记并说明理由（1.1 依赖规范）。
