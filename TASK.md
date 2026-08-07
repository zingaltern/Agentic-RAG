# TASK.md — 任务简报模板

> 用法：每次让 Agent 开始新一轮开发前，复制本文件为 `TASK.md`（覆盖），填写第 1 节。
> 第 2~3 节是 Agent 开工前与收工前必须逐条自检的清单，不要删除。

---

## 1. 本次任务（由用户/调度方填写）

**里程碑**：`W1 / W2 / W3 / W4`（对应 `DEVELOPMENT.md` 第 9 章）

**本次要实现的模块（可多选）**：

- [ ] 工程骨架 / Config / 接口契约（`DEVELOPMENT.md` 第 1、3、4 章）
- [ ] Parser + Chunker（第 5.1、5.2）
- [ ] VectorEngine（BGE-small-zh-v1.5）+ VectorStore（Chroma）（第 5.3、3.2）
- [ ] Retriever（RRF 混合检索）（第 5.4、3.3）
- [ ] Ingestion 上传流水线 + Jobs（第 5.10、6.2、6.3）
- [ ] Orchestration 反思循环 + Evaluator/Rewriter（第 5.5、5.6、5.9）
- [ ] 答案生成（DeepSeek）+ 幻觉检测（第 5.7、5.8、3.8、3.7）
- [ ] `/api/query` 全链路 API（第 6.4）
- [ ] `/ui` 测试页五区块（第 7 章）
- [ ] 测试补齐 + `rage_eval` 评测（第 8 章）
- [ ] 其他：______

**验收判据（DoD，务必可验证）**：____________________________

**特殊要求/已知约束**：____________________________

---

## 2. 开工自检（Agent 逐条确认后再动手）

- [ ] 已按任务范围读取 `DEVELOPMENT.md` 对应章节（含附录 A 执行要点）
- [ ] 理解了接口签名与数据模型，无自行发挥
- [ ] 环境变量 `RAG_DEEPSEEK_API_KEY` 可用（本任务涉及 LLM 时）
- [ ] 明确了本次改动的文件边界（不顺手重构无关代码）
- [ ] 数据库/模型文件路径符合 `settings.yaml` 默认（`data/`、HF 缓存）

## 3. 收工自检（Agent 完成前必须全绿）

- [ ] `make test` 全绿，新增用例覆盖本次模块（对照 `DEVELOPMENT.md` 8.1 矩阵）
- [ ] `make run` 可启动，`GET /ui` 返回 200（涉及 UI/API 时）
- [ ] 已用真实文档跑通对应场景（涉及链路时）
- [ ] 无密钥泄漏、无 `.env`/`data/` 入库；`git status` 内容符合预期
- [ ] README 或相关注释已同步（若行为有变化）
- [ ] 对照第 1 节 DoD 逐项报告结果，未达成项明确列出

---

## 4. 里程碑参考（`DEVELOPMENT.md` 第 9 章）

| 周 | 交付 | 验收 |
|---|---|---|
| W1 | 工程骨架、接口、Config、Parser+Chunker、UI 骨架 | 契约测试通过；`/ui` 200 |
| W2 | VectorEngine+VectorStore+Retriever+上传 Job | demo-ingest 通过；UI 上传走通 |
| W3 | Orchestration+Detector+`/api/query`+DeepSeek | 问答返回引用与迭代数 |
| W4 | 全量测试、集成、README、可选 docker | pytest 绿、docker-compose 可用 |

（正文完）