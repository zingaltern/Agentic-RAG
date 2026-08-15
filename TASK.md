# TASK.md — 任务简报模板

> 用法：每次让 Agent 开始新一轮开发前，复制本文件为 `TASK.md`（覆盖），填写第 1 节。
> 第 2~3 节是 Agent 开工前与收工前必须逐条自检的清单，不要删除。

---

## 1. 本次任务（由用户/调度方填写）

**里程碑**：`W4`（对应 `DEVELOPMENT.md` 第 9 章）

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
- [x] 测试补齐 + `rage_eval` 评测（第 8 章）
- [ ] 其他：______

**验收判据（DoD，务必可验证）**：
1. pytest 全绿且行覆盖率 ≥80%（`make test` 带 `--cov` 门禁，`fail_under=80`）；
2. `make eval` 可运行且 `reports/eval-*.md` 非空（§8.3，Hit@5 等指标如实列出低分项）；
3. `pytest -m integration` 全链路可跑（§8.2：临时 Chroma + 本地 BGE + Stub LLM，不赖外网/真 key）；
4. `docker compose up` 起 api 后 `/api/health` 全 ok（§10，DoD）；
5. README 与实现一致（§10.3）；reports/ 产物不入库。

**特殊要求/已知约束**：
- 不动算法/SPEC；只做：rageval CLI、集成测试、pytest-cov 门禁、Dockerfile+compose、README、Makefile（eval/coverage/docker 目标）。
- 默认 pytest 过滤 integration（`-m "not integration"`）；集成测试单独验证。
- 镜像内 `HF_ENDPOINT=https://hf-mirror.com` 兜底（避免模型加载卡外网）；DeepSeek key 走 `RAG_DEEPSEEK_API_KEY` 注入（.env，gitignored）。
- 覆盖门禁逐模块补测，难覆盖分支 `# pragma: no cover` 少用并说明。

---

## 2. 开工自检（Agent 逐条确认后再动手）

- [ ] 已按任务范围读取 `DEVELOPMENT.md` 对应章节（含附录 A 执行要点）
- [ ] 理解了接口签名与数据模型，无自行发挥
- [ ] 环境变量 `RAG_DEEPSEEK_API_KEY` 可用（本任务涉及 LLM 时）
- [ ] 明确了本次改动的文件边界（不顺手重构无关代码）
- [ ] 数据库/模型文件路径符合 `settings.yaml` 默认（`data/`、HF 缓存）

## 3. 收工自检（Agent 完成前必须全绿）

- [ ] `make test` 全绿（现网口径 293 passed，含覆盖率门禁 ≥80%），新增用例覆盖本次模块
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
| W5~W8 | Phase2 全交付（详见 `DEVELOPMENT.md` 13.9）+ V5.1 团队模式 | 均已交付；涉及指标的功能改动须 `make eval` 前后对比（13.1） |

（正文完）