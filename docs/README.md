# 文档索引（`docs/`）

> 本目录是项目文档的唯一入口；**规格来源仍是根目录 `DEVELOPMENT.md`**（权威顺序见 `AGENTS.md` 的「文档体系」）。
> 引用约定：项目内一律按**文件名**引用本目录文档（如源码 docstring 的「在线侧输入处理.md L1」、
> 任务书的《数据清洗与格式_开发要点.md》），因此目录调整不会使既有引用失效；新增引用请带目录路径。

## 一、目录结构

```
docs/
├── README.md                       # 本文件：文档索引与归档约定
├── review-followup.md              # 专业评审延后项清单（P0/P1/P2 分级）
├── RAG系统介绍.html                 # 功能与特殊场景处理一览（对外介绍页，浏览器直接打开）
├── specs/                          # 方案/规格类输入文档（外部评审或蓝图，落地前先读）
│   ├── 在线侧输入处理.md             # 输入侧治理蓝图（L0~L4 分层；13.12/13.13 落地依据）
│   ├── 数据清洗与格式_开发要点.md     # 数据清洗与格式边界执行规格（15.2/15.4 P-A/P-B 依据）
│   └── 测试与评测体系设计.md         # 测试分层/模块功能与质量矩阵/指标与数据集治理（设计稿，待评审落地）
└── dev-tasks/                      # 开发指令（按批次编号，两位序号递增）
    ├── 02-P-D-结构化Agent.md
    ├── 03-P3-可选适配器包.md
    ├── 04-遗留项与元数据富化.md
    ├── 08-查询智能补强批次.md
    └── archive/                    # 已交付批次归档（文件名不变；状态明细见 DEVELOPMENT.md）
        ├── 05-移除A4多库自动路由.md
        ├── 06-输入侧治理激活批次.md
        └── 07-离线侧补强批次P0-P3.md
```

## 二、按用途索引

### 规格与方案（`docs/specs/`）

| 文件 | 作用 | 主要引用方 |
|---|---|---|
| `specs/在线侧输入处理.md` | 输入侧治理方案（L0 极简通道 / L1 标准化 / L2 会话状态 / L3 路由分解 / L4 结构化提取；含非功能指标；L0~L2 已落地 13.12/13.13，L3/L4 规划） | `DEVELOPMENT.md` 13.12–13.13、`app/core/config.py`、`app/adapters/normalizer`、`app/adapters/dialogue`、`app/services/session_store.py` |
| `specs/数据清洗与格式_开发要点.md` | 清洗层与输入格式边界的执行规格（T1–T7，均已交付） | `DEVELOPMENT.md` 第 15 章、`docs/dev-tasks/04` |
| `specs/测试与评测体系设计.md` | 测试六层模型、模块功能/质量测试矩阵、指标分层与统计规范、数据集治理与实施路线（设计稿） | `AGENTS.md`、`Makefile`、`tests/*/README.md` |

### 开发指令（`docs/dev-tasks/`）

**在办 / 待排期**：

| 文件 | 批次主题 | 依据 | 状态 |
|---|---|---|---|
| `dev-tasks/02-P-D-结构化Agent.md` | P-D 真 Agentic（结构化 Agent，单独立项大改造） | `DEVELOPMENT.md` 15.1/15.4/15.5 | 未开工 |
| `dev-tasks/03-P3-可选适配器包.md` | 13.10 P3 可选适配器（OCR/表格/多分块/多 embedding/多 LLM，默认关） | `DEVELOPMENT.md` 13.10/15.2 | 未开工 |
| `dev-tasks/04-遗留项与元数据富化.md` | 14.4 遗留项 + 元数据富化（T5） | `DEVELOPMENT.md` 14.4 | 部分交付（T5 / CI / 事件钩子已完成） |
| `dev-tasks/08-查询智能补强批次.md` | 查询智能 Q1 多策略改写 / Q2 Scope 路由 lite | 2026-09-06 企业级平台对标评估 | Q1 实现完成（随代码在途）；Q2 未开工 |

**已归档（`docs/dev-tasks/archive/`，均已交付）**：

| 文件 | 批次主题 | 交付日期 |
|---|---|---|
| `dev-tasks/archive/05-移除A4多库自动路由.md` | 移除 A4 多库自动路由层（多库基础设施保留） | 2026-08-30 |
| `dev-tasks/archive/06-输入侧治理激活批次.md` | 输入侧治理激活 V1（T1 绝望短路 / T2 极简通道扩容 / T3 服务端会话接通） | 2026-08-30 |
| `dev-tasks/archive/07-离线侧补强批次P0-P3.md` | 离线侧补强 P0–P3（8 项，解析/清洗/分块方向） | 2026-09-06 |

> 各批次的**交付状态明细**统一登记在 `DEVELOPMENT.md` 对应章节，此表只做快览。

### 其他

| 文件 | 作用 | 必读性 |
|---|---|---|
| `review-followup.md` | 核实存在但决定延后的评审问题（问题 → 修复/理由 → 日期） | 低 |
| `RAG系统介绍.html` | 功能与特殊场景处理一览（面向介绍/演示的单页 HTML） | 低 |

### 根目录文档（不在 `docs/`，但同属文档体系）

| 文件 | 作用 | 必读性 |
|---|---|---|
| `DEVELOPMENT.md` | **唯一规格来源**：接口契约、数据模型、算法、配置、API、验收 | 高 |
| `TASK.md` | 当前任务简报（里程碑与验收判据） | 高 |
| `README.md` | 快速启动与目录速览 | 中 |
| `PRODUCT_MANUAL.md` | 产品说明书（面向终端用户） | 中 |
| `AGENTS.md` | 工程规范与文档体系入口 | 高 |
| `tests/eval/README.md`、`tests/review/README.md`、`tests/eval_suite/README.md` | 两层评测策略（验证集 / 外部测试集 / 冻结基准集）规范 | 评测时必读 |

## 三、归档约定

1. **根目录只放工程骨架与顶层文档**（`app/`、`tests/`、`frontend/`、`web/`、`configs/`、`Makefile`、顶层 5 份 md）；新增设计/说明文档一律进 `docs/`。
2. 外部输入的**方案/规格类**文档 → `docs/specs/`；**开发指令** → `docs/dev-tasks/NN-短名.md`（序号递增，不复用）；批次**交付后迁入 `docs/dev-tasks/archive/`**（文件名不变，状态明细仍以 `DEVELOPMENT.md` 为准）。
3. 评测/性能/外部基准报告 → `reports/`（已 gitignore，产物不入库），命名 `eval-`/`review-`/`perf-`/`benchmark-{时间戳}.md`。
4. 文档与代码冲突时以 `DEVELOPMENT.md` 为准；需要变更规格时**先改 `DEVELOPMENT.md` 再改代码**。
5. 不留临时产物：构建日志、`.DS_Store`、缓存目录均在 `.gitignore` 中，不入库。
