# 专业评审跟进：延后修复项清单

> 来源：专业评估人员评审（按严重度 P0/P1/P2 分级）。
> 本文件只收录【核实存在但决定延后】的项目；已修复项状态随批次更新，归档见文末。
> 每条含：问题 → 修复/理由 → 日期。
> 截至 2026-10-19，**未完成延后项**：D2 余项（多 worker 全局限速）、D3 余项（RBAC 深度扩展）、
> D4 余项（多用户部署凭据存储）、D6（子块落库）、D12–D19（07 批次延后项）。
> 已完成项（D1/D5/D7–D11）与 2026-08-14 即时修复项见文末「已完成归档」。

## 一、未完成延后项

### D2. 认证默认关闭 + collection 直传绕过 kb 校验 + 无速率限制（评估 P0-3）——部分完成（2026-08-15）
- **核实**：`configs/settings.yaml` `auth_enabled: false`（注：后经用户方启用为 true，属部署配置）；`app/api/query.py` 曾允许 `collection` 直传；曾无任何限速。
- **已完成（V5.1 并发与安全加固批次）**：
  - **速率限制子项 ✅ 已完成**：进程内滑动窗口限速器（`app/core/rate_limit.py`，纯标准库）对 `/api/query`（20 次/分/IP）与 `/api/documents/upload`（10 次/分/IP）生效，超限返回 43002 契约包；阈值走 `configs/settings.yaml`（`api.query_rate_per_min`/`upload_rate_per_min`）。
  - `collection` 直传校验 ✅：`app/api/query.py` 对 `collection` 直传先经 KbStore 反查库存在（不存在 → 40001）。
  - 并发信号量 ✅：`orchestration.max_concurrent_queries`（默认 4），耗尽/等待超时返回 43001。
- **剩余**：多 worker 部署时进程内限速局限（各进程独立计数，已注释说明），如需全局限速再评估 Redis（无日期）。

### D3. RBAC 死代码 + X-User-Id 前端不发（评估 P2-2）——部分完成（2026-08-14）
- **核实**：`app/services/rbac.py` 无任何路由引用；`X-User-Id` 前端从不发送，审计恒为 `anonymous`。
- **已完成**：列表按库过滤（`/api/documents?kb_id=` + 前端下拉联动，P0-1 批次）；V5.1 起 Authorizer 已接线（kb 级 owner/scope 权限生效）。
- **延后理由**：文档级 RBAC 拦截属多租户预留接口（`DEVELOPMENT.md` 13.11.3），单用户产品无实际诉求；接口与注入点已就绪，启用成本低。
- **计划修复日期**：多用户部署时（原预计 2026-09 后；截至 2026-10-19 未触发，仍延后）。

### D4. API Key 存 localStorage 明文（评估 P2-3）—— 已随机制移除失效（2026-08-15）
- **核实**：`frontend/src/api/base.js` 曾从 `localStorage` 读取 `api_key`（明文）。
- **状态更新（V5.1）**：V5.1 以「密码登录 + HMAC Token（`localStorage.auth_token`）」取代 API Key 机制，`api_key` 存储路径已删除，本遗留自动失效；Token 为本机会话凭证，威胁模型等同原结论（无跨站脚本向量，桌面+静态托管）。
- **仍属延后**：多用户 Web 部署时的凭据存储加固（如 SafeStorage 经 IPC）再评估（原预计 2026-09+；截至 2026-10-19 未触发）。

### D6. 父子分块"空架子"：子块从不落库（评估 P2-6）
- **核实**：`ingestion.py:104` 只存父块；`orchestration._backtrack_parent_chunks` 在"只存父块"模式下实际 no-op。但这是 `DEVELOPMENT.md` 5.10 的**明确设计决策**（"W2 只存父块，子块文本供上下文回溯，本阶段不落库"），非 bug。
- **延后理由**：当前父块粒度（512 token）对现有文档已够用；子块落库是"精细检索"增强，需同时改动分块/入库/检索三层，收益不确定。
- **计划修复日期**：如评测显示检索精度不足再做（暂无日期）。

## 二、07 离线侧补强批次延后项（2026-09-06，`docs/dev-tasks/archive/07-离线侧补强批次P0-P3.md` §3）

> 以下为本批次明确不做项（核实存在、决定延后；触发条件即计划修复日期）。

| # | 项 | 延后理由 | 触发条件 |
|---|---|---|---|
| D12 | 文档级 acl_tags 向量库强制 filter | 依赖向量库 metadata filter 能力扩展（Chroma 标量等值之外） | 文档级权限需求立项（12.3 深度 C） |
| D13 | 表格 JSON 字段级 metadata 过滤 | Chroma 标量 metadata 不支持 JSON 内字段查询；v1 已随 chunk 携带 table_json 供生成阶段 | "对比/聚合表格字段"类查询成为高频诉求 |
| D14 | xlsx 表格双轨存储 | 整文档即表，行级结构完整；改造收益低 | xlsx 表格类问答质量实测不足 |
| D15 | 分片上传 / 断点续传 | 单机 ≤50MB 场景无阻塞；改造涉及 API 契约与前端 | 大文件（>100MB）成为常态 |
| D16 | 上传病毒扫描 | 解析侧已有魔数+zip 炸弹防御；病毒扫描需引擎依赖 | 面向不可信来源开放上传时 |
| D17 | 影子索引灰度切换（重建零停机） | v1 rebuild 已含失败回滚 + "重建期间暂停上传"语义 | 多实例/高可用部署时 |
| D18 | 入库链路 MQ 化 | 进程内有界队列 + 背压（43003）满足单机；接口已按 Redis/MQ 可替换设计 | 多 worker/跨机部署时 |
| D19 | 归档磁盘配额与清理策略 | 归档默认关；单机磁盘监控由部署方管理 | 归档开启且磁盘成为约束时 |

## 三、已完成归档

### 已完成延后项（V5.1 批次）

| # | 问题 | 修复 | 日期 |
|---|---|---|---|
| D1 | 文档索引纯内存，重启后幽灵文档（P0-2） | `DocumentsStore` 持久化到 `data/documents.json`（content_hash 幂等）；启动时 JSON 缺失且 Chroma 有数据则 `rebuild_from_vector_store` 全量重建；列表支持 `kb_id` 过滤 | 2026-08-14 |
| D5 | TOCTOU：并发上传同文件可双份入库（P2-5） | `IngestionPipeline` 增加 per-content_hash 进程锁（`_lock_for`），幂等检查与向量写入原子化 | 2026-08-15 |
| D7 | 30s 请求预算为软约束（P2-9） | `orchestration.py` 在 LLM 调用后与幻觉检测后各补一次 `_check_budget`，超时抛 21002 | 2026-08-14 |
| D8 | 打包无 splash，就绪前黑屏（P1-6） | `main.cjs` 就绪前显示 380×220 splash；后端意外退出自动重启（最多 2 次），恢复失败才弹错强退 | 2026-08-15 |
| D9 | plugins/switch 占位恒抛 50001（P2-1） | `/api/plugins/switch` 实现热切换（校验→深拷贝→`wire` 重建→原子替换，失败回滚）；写操作需登录且仅管理员 | 2026-08-15 |
| D10 | 桌面惯例：window-all-closed 直接 quit（P2-8） | macOS 关窗保留 Dock 与后端进程，`activate` 重建窗口；非 macOS 关窗即退；`Cmd+Q` 级联杀后端 | 2026-08-15 |
| D11 | 上传进度条缺失 + pollTimer 轮询泄漏（P1-2） | `DocumentsView.vue` XHR 真实百分比 + 入库不确定态进度条；轮询 interval 登记并在 `onUnmounted` 统一清理 | 2026-08-15 |

### 已即时修复（2026-08-14 评审整改）

| 项 | 修复内容 |
|---|---|
| P0-1 多库删除 bug | `DocEntry` 增 `kb_id`；删除按文档所属库解析 collection |
| P0-4 上传无预检 | upload 增加 Content-Length 预检（保留读后兜底） |
| P1-1 三处 loading 卡死 | DocumentsView/KbsView 加 try/finally；QueryConsole 加 catch 错误气泡 |
| P1-3 history 含当前问题 | history 在 push 前构建（不含当前问题） |
| P1-4 删库无确认 | KbsView 删除加 el-popconfirm 确认 |
| P1-5 前端无超时 | api.js 加 90s AbortController 超时 |
| P2-4 catch-all 吞 /api/* | spa_fallback 对 /api/* 抛 404；HTTPException 统一 {code,message} 契约 |
| P2-7 死代码（部分） | 删除 `queryUrl`、`pollTimer` 死变量 |
| P2-10 README 过时 | W5~W8 状态同步为"已落地" |
