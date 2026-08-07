# TASK.md — 任务简报模板

> 用法：每次让 Agent 开始新一轮开发前，复制本文件为 `TASK.md`（覆盖），填写第 1 节。
> 第 2~3 节是 Agent 开工前与收工前必须逐条自检的清单，不要删除。

---

## 1. 本次任务（由用户/调度方填写）

**里程碑**：`W3`（对应 `DEVELOPMENT.md` 第 9 章）

**本次要实现的模块（可多选）**：

- [ ] 工程骨架 / Config / 接口契约（`DEVELOPMENT.md` 第 1、3、4 章）
- [ ] Parser + Chunker（第 5.1、5.2）
- [ ] VectorEngine（BGE-small-zh-v1.5）+ VectorStore（Chroma）（第 5.3、3.2）
- [ ] Retriever（RRF 混合检索）（第 5.4、3.3）
- [ ] Ingestion 上传流水线 + Jobs（第 5.10、6.2、6.3）
- [x] Orchestration 反思循环 + Evaluator/Rewriter（第 5.5、5.6、5.9）
- [x] 答案生成（DeepSeek）+ 幻觉检测（第 5.7、5.8、3.8、3.7）
- [x] `/api/query` 全链路 API（第 6.4）
- [x] `/ui` 测试页五区块（第 7 章）
- [ ] 测试补齐 + `rage_eval` 评测（第 8 章）
- [ ] 其他：______

**验收判据（DoD，务必可验证）**：
1. `make test` 全绿（新增 Detector 4 组 / Evaluator 阈值两侧+空上下文 / Orchestration Mock 3 次循环+超时+21001 兜底 / LLM mock / /api/query 路由单测）；
2. `curl POST /api/query '{"query":"入职满一年可休几天年假？"}'` → code:0、answer 含 [1] 引用、sources 含 员工手册.md、citations.used 非空、iterations≥0；
3. `curl /api/health` → llm=ok（装配态；llm 未装配时 degraded 不崩溃）；
4. `/ui` 问答控制台真实返回回答+引用+来源+迭代数+耗时+幻觉徽标，debug 折叠面板显示 debug_trace；
5. 无密钥泄漏（api_key 只走环境变量）。

**特殊要求/已知约束**：
- api_key 只从 `RAG_DEEPSEEK_API_KEY`/`RAG_LLM_API_KEY` 环境变量（.env 已有真实 key），缺失时 wire 优雅降级（llm=None → health not_loaded/degraded），chat 时抛 LLMUnavailableError(30001)。
- deepseek-reasoner 忽略 reasoning_content 只取 content；超时 30s（llm.timeout_seconds）。
- 编排同步实现：整体 `request_timeout_seconds=30` 与检索轮次 `search_timeout_seconds=10` 采用时间预算检查（每阶段校验 elapsed），超时抛 21002。
- `sources[].filename` = 入库时 `Chunk.metadata.source` 原始文件名（6.4 关键契约）；`chunk` 字段文本截断 300 字符（模块内常量，非配置）。
- filters/collection 透传并贯穿反思循环；collection 非默认值 → ValidationError（MVP 单库）。
- 反思超限 → code=21001 + 兜底文案「抱歉，暂未找到相关答案…」。
- synonyms.csv 缺失/损坏 → 内置小词表兜底，启动不失败。
- 集成手测：DeepSeek 外网可达直接调；嵌入模型走 HF 缓存（HF_ENDPOINT 镜像已有缓存，无需下载）。

---

## 2. 开工自检（Agent 逐条确认后再动手）

- [ ] 已按任务范围读取 `DEVELOPMENT.md` 对应章节（含附录 A 执行要点）
- [ ] 理解了接口签名与数据模型，无自行发挥
- [ ] 环境变量 `RAG_DEEPSEEK_API_KEY` 可用（本任务涉及 LLM 时）
- [ ] 明确了本次改动的文件边界（不顺手重构无关代码）
- [ ] 数据库/模型文件路径符合 `settings.yaml` 默认（`data/`、HF 缓存）

## 3. 收工自检（Agent 完成前必须全绿）

- [x] `make test` 全绿（66 passed），新增用例覆盖本次模块（contracts/config/domain/parser/chunker/ui/logging）
- [x] `make run` 可启动，`GET /ui` 返回 200（StaticFiles 目录重定向 `/ui`→`/ui/`，浏览器/curl -L 均 200）
- [x] 已用真实文档跑通对应场景（6 格式 parser 用 pymupdf/docx/openpyxl/pptx 真实生成样本单测）
- [x] 无密钥泄漏、无 `.env`/`data/` 入库；`git status` 内容符合预期（app/web/configs/tests 新文件 + 两份文档修订）
- [x] README 或相关注释已同步（新增 README 快速开始）
- [x] 对照第 1 节 DoD 逐项报告结果：
  1. 契约测试（domain 模型字段/接口签名/错误码表/PluginRegistry/config 校验）全绿 ✅
  2. `/ui` 200、`/api/health` 统一响应包（W1 组件未装配 → degraded/not_loaded）✅
  3. Parser 六格式 + 损坏/不支持抛 `ParseError(30003)` ✅
  4. Chunker 父子规则（≤512/≤128 tokens、parent_id 关联、sha256[:16]）✅
  5. 无密钥泄漏、`data/`/`.env` 已 gitignore ✅
  未达成项：无（`/api/upload|query|jobs` 按计划留桩，W2/W3 填实）

---

## 4. 里程碑参考（`DEVELOPMENT.md` 第 9 章）

| 周 | 交付 | 验收 |
|---|---|---|
| W1 | 工程骨架、接口、Config、Parser+Chunker、UI 骨架 | 契约测试通过；`/ui` 200 |
| W2 | VectorEngine+VectorStore+Retriever+上传 Job | demo-ingest 通过；UI 上传走通 |
| W3 | Orchestration+Detector+`/api/query`+DeepSeek | 问答返回引用与迭代数 |
| W4 | 全量测试、集成、README、可选 docker | pytest 绿、docker-compose 可用 |

（正文完）