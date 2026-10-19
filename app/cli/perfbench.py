"""性能基准 CLI（DEVELOPMENT.md 8.5.2，`make perf` 底层）：进程内（真实 BGE + Chroma，
Stub LLM）按文档数规模点测入库吞吐 / 查询延迟 / Hit@5 曲线，出 reports/perf-{ts}.md，
可 --baseline 对比 Δ。

口径（与 rageval 8.4 同源）：
- 合成语料进程内确定性生成（固定 seed，random.Random），不入仓不落盘；
  语料按文档序号前缀一致（前 N 份与整体语料前 N 份完全相同），
  查询集固定取自最小规模点语料事实并跨所有规模点复用 → Hit@5 随规模可比；
- 每个规模点开测前清空该库向量集合与文档幂等表（等价 8.4 --fresh-kb 级联），
  否则 content_hash 幂等命中会跳过重嵌、吞吐计时失真；
- 暖机（模型加载 + BM25 索引构建）显式排除，不计入延迟样本；
- 阶段拆解按 Job.stage_timings（自 t0 累计 ms）相邻差分（stage_diffs）。

用法：
    python -m app.cli.perfbench --scales 50,150,300 --queries 30 --seed 42
    python -m app.cli.perfbench --baseline reports/perf-<基线>.md
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KB = "perf"

# Job.stage_timings 的阶段键与记录顺序（ingestion.py _stage：自 t0 累计 ms）
STAGE_ORDER = ("parse", "clean", "chunk", "embed", "index")

# 跨规模汇总表的指标列（键=列名，值=渲染格式）；Hit@5 以 0~1 分数参与计算
SUMMARY_METRICS = ("入库吞吐(docs/s)", "avg(ms)", "P50(ms)", "P90(ms)", "P95(ms)", "max(ms)", "Hit@5")
SUMMARY_FMT = {
    "入库吞吐(docs/s)": "{:.2f}",
    "avg(ms)": "{:.1f}",
    "P50(ms)": "{:.1f}",
    "P90(ms)": "{:.1f}",
    "P95(ms)": "{:.1f}",
    "max(ms)": "{:.1f}",
    "Hit@5": "{:.1%}",
}
# 基线 Δ 列短名（列名以 Δ 开头，_parse_baseline 据此跳过 Δ 列）
SUMMARY_SHORT = {
    "入库吞吐(docs/s)": "吞吐",
    "avg(ms)": "avg",
    "P50(ms)": "P50",
    "P90(ms)": "P90",
    "P95(ms)": "P95",
    "max(ms)": "max",
    "Hit@5": "Hit@5",
}


# -- 指标纯函数 ----------------------------------------------------------------

def percentile(values: list[float], p: float) -> float:
    """线性插值百分位（不引 numpy）。

    - 空列表 / p 不在 [0,100] → ValueError；
    - p=0 → 最小值，p=100 → 最大值，单元素列表恒为该元素。
    """
    if not values:
        raise ValueError("percentile：空列表")
    if not 0 <= p <= 100:
        raise ValueError(f"percentile：p 越界（{p}）")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (p / 100) * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    frac = rank - lower
    return float(ordered[lower]) + (float(ordered[upper]) - float(ordered[lower])) * frac


def parse_scales(raw: str) -> list[int]:
    """解析 --scales（逗号分隔文档数规模点）→ 升序去重断言后的 int 列表；非法输入报错。"""
    if not raw or not raw.strip():
        raise ValueError("--scales 不能为空（示例：50,150,300）")
    scales: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            raise ValueError(f"--scales 含空项（{raw!r}）")
        try:
            value = int(token)
        except ValueError as exc:
            raise ValueError(f"--scales 含非整数项：{token!r}") from exc
        if value <= 0:
            raise ValueError(f"--scales 必须为正整数：{token!r}")
        scales.append(value)
    if len(set(scales)) != len(scales):
        raise ValueError(f"--scales 含重复规模点（{raw!r}）")
    return sorted(scales)


def stage_diffs(timings: dict) -> dict[str, int]:
    """Job.stage_timings（自 t0 累计 ms）→ 各阶段自身耗时 ms（相邻差分，按 STAGE_ORDER）。"""
    diffs: dict[str, int] = {}
    prev = 0
    for name in STAGE_ORDER:
        cur = int(timings.get(name, prev))
        diffs[name] = max(0, cur - prev)
        prev = cur
    return diffs


# -- 合成语料（进程内确定性生成，不入仓不落盘）----------------------------------

@dataclass
class DocSpec:
    """一份合成文档：文件名、正文、可提问的唯一事实（编号实体）与对应 direct 问题。"""

    filename: str
    text: str
    fact: str
    question: str


# 事实素材：取值随文档序号轮转，实体编号全局唯一，filler 不含编号避免歧义
_POLICY_ITEMS = (
    ("差旅住宿报销上限", ("每晚 400 元", "每晚 450 元", "每晚 500 元")),
    ("单笔采购审批时限", ("5 个工作日", "3 个工作日", "7 个工作日")),
    ("年度带薪年假天数", ("10 天", "12 天", "15 天")),
)
_PRODUCT_MODELS = ("VR", "TA", "EX", "NB")
_PRODUCT_WARRANTY = (12, 18, 24)
_OPS_CAUSES = ("磁盘阵列故障", "核心交换机网络抖动", "配置漂移", "证书过期未续期", "内存泄漏")
_OPS_HOURS = (2, 4, 6, 8, 12)

_FILLER = (
    (  # 制度域通用叙述
        "基准科技人力资源部定期回顾各项制度的执行情况",
        "制度修订需经管理例会评审并同步全体部门",
        "各部门负责人应组织新员工完成入职制度学习",
        "制度条文解释权归基准科技行政管理部所有",
        "员工可通过内网门户查阅最新版本的制度文件",
    ),
    (  # 产品域通用叙述
        "基准科技智能终端产品线覆盖办公与家用多个场景",
        "产品出厂前需通过整机老化与安规双重测试",
        "售后服务网络已覆盖全国主要城市与区县",
        "用户注册产品序列号后可享受延长服务权益",
        "固件升级采用差分包方式以节省下载流量",
    ),
    (  # 运维域通用叙述
        "值班工程师按轮班表执行每日两次的例行巡检",
        "告警信息统一接入监控平台并按级别分派",
        "变更操作须在变更窗口内完成并留存回滚方案",
        "复盘会议形成的改进项由责任人限期闭环",
        "机房动环数据每五分钟采集一次并留存半年",
    ),
)
_FILLER_COMMON = (
    "基准科技持续投入研发并以质量管理体系为支撑",
    "本文档内容属于公司内部资料请注意保密",
    "相关流程的电子化表单已在协同平台上线",
    "如对内容有疑问请联系对应的归口管理部门",
)


def make_doc(rng: random.Random, i: int) -> DocSpec:
    """生成第 i 份文档（仅依赖 rng 消费顺序与 i → 语料前缀一致，规模 N 无关）。"""
    topic = i % 3  # 制度 / 产品 / 运维纪要轮转
    if topic == 0:  # 制度：编号实体 HR-xxx
        code = f"HR-{i:03d}"
        item, pool = _POLICY_ITEMS[(i // 3) % len(_POLICY_ITEMS)]
        value = pool[(i // 3) % len(pool)]
        fact = f"《基准科技员工制度汇编》（制度编号 {code}）明确：{item}为{value}。"
        question = f"制度编号 {code} 规定的{item}是多少？"
        title = "行政管理制度"
    elif topic == 1:  # 产品：编号实体 基准-XX-nnn（对齐 8.5.2 示例口径）
        code = f"基准-{_PRODUCT_MODELS[(i // 3) % len(_PRODUCT_MODELS)]}-{i:03d}"
        months = _PRODUCT_WARRANTY[(i // 3) % len(_PRODUCT_WARRANTY)]
        fact = f"{code} 设备的保修期为 {months} 个月，自验收签字之日起算。"
        question = f"{code} 设备的保修期是多长时间？"
        title = "产品说明文档"
    else:  # 运维纪要：编号实体 OPS-nnnn
        code = f"OPS-{i:04d}"
        cause = _OPS_CAUSES[(i // 3) % len(_OPS_CAUSES)]
        hours = _OPS_HOURS[(i // 3) % len(_OPS_HOURS)]
        fact = f"工单 {code}（{cause}）处置时长 {hours} 小时，已闭环归档。"
        question = f"工单 {code} 的处置时长是多少小时？"
        title = "运维值班纪要"

    filename = f"{code}.md" if i % 2 == 0 else f"{code}.txt"
    head = f"# 基准科技{title}（编号 {code}）\n\n本文档为基准科技内部资料，编号 {code}，仅供归口同事查阅。\n"
    filler_pool = list(_FILLER[topic]) + list(_FILLER_COMMON)
    target = 600 + rng.randrange(400)  # 正文目标 600~1000 字 → 全文约 650~1100 字
    body: list[str] = []
    body_len = 0
    while body_len < target:
        sents = [rng.choice(filler_pool) for _ in range(2 + rng.randrange(3))]
        para = "。".join(sents) + "。"
        body.append(para)
        body_len += len(para)
    body.insert(min(2, len(body)), fact)  # 事实句独立成段（插入正文前部）
    return DocSpec(filename=filename, text=head + "\n\n".join(body) + "\n",
                   fact=fact, question=question)


def generate_corpus(seed: int, n: int) -> list[DocSpec]:
    """固定 seed 确定性生成 n 份文档；前 N 份与更大语料的前 N 份逐字节一致（前缀一致）。"""
    rng = random.Random(seed)
    return [make_doc(rng, i) for i in range(n)]


def build_query_set(docs: list[DocSpec], n: int) -> list[dict]:
    """从语料事实构造固定 direct 查询集（expected_sources=该文档名，均匀抽样覆盖三类主题）。"""
    if n <= 0:
        raise ValueError("查询条数必须为正整数")
    n = min(n, len(docs))
    step = len(docs) / n
    idxs = sorted({min(len(docs) - 1, int(k * step)) for k in range(n)})
    return [{"id": f"P{k:03d}", "query": docs[j].question,
             "expected_sources": [docs[j].filename]} for k, j in enumerate(idxs)]


# -- 单规模点结果与指标 ----------------------------------------------------------

@dataclass
class ScaleResult:
    """一个规模点的实测结果（docs=成功入库数；latencies_ms 只含正常完成样本）。"""

    scale: int
    docs: int
    chunks: int
    ingest_seconds: float
    stage_ms: dict = field(default_factory=dict)   # 各阶段累计差分 ms（stage_diffs 求和）
    latencies_ms: list = field(default_factory=list)
    queries: int = 0
    hits: int = 0
    errors: int = 0
    failed_docs: int = 0


def summary_metrics(r: ScaleResult) -> dict[str, float]:
    """跨规模汇总口径的指标值（键=SUMMARY_METRICS；Hit@5 为 0~1 分数）。"""
    lat = r.latencies_ms
    n = max(1, len(lat))
    return {
        "入库吞吐(docs/s)": (r.docs / r.ingest_seconds) if r.ingest_seconds > 0 else 0.0,
        "avg(ms)": sum(lat) / n,
        "P50(ms)": percentile(lat, 50),
        "P90(ms)": percentile(lat, 90),
        "P95(ms)": percentile(lat, 95),
        "max(ms)": percentile(lat, 100),
        "Hit@5": (r.hits / r.queries) if r.queries else 0.0,
    }


def stage_share(r: ScaleResult, stage: str) -> float:
    """某阶段耗时占全部阶段差分之和的比例（入库墙钟含幂等/存储开销，占比按阶段总和更可比）。"""
    total = sum(r.stage_ms.values())
    return (r.stage_ms.get(stage, 0) / total) if total else 0.0


def _scale_rows(r: ScaleResult) -> list[tuple[str, str]]:
    """逐规模点明细表行（| 指标 | 值 |）。"""
    sm = summary_metrics(r)
    return [
        ("文档数", f"{r.scale}"),
        ("成功入库", f"{r.docs}"),
        ("chunks", f"{r.chunks}"),
        ("入库总耗时(s)", f"{r.ingest_seconds:.2f}"),
        ("入库吞吐(docs/s)", SUMMARY_FMT["入库吞吐(docs/s)"].format(sm["入库吞吐(docs/s)"])),
        ("embed占比", f"{stage_share(r, 'embed'):.1%}"),
        ("parse占比", f"{stage_share(r, 'parse'):.1%}"),
        ("avg(ms)", SUMMARY_FMT["avg(ms)"].format(sm["avg(ms)"])),
        ("P50(ms)", SUMMARY_FMT["P50(ms)"].format(sm["P50(ms)"])),
        ("P90(ms)", SUMMARY_FMT["P90(ms)"].format(sm["P90(ms)"])),
        ("P95(ms)", SUMMARY_FMT["P95(ms)"].format(sm["P95(ms)"])),
        ("max(ms)", SUMMARY_FMT["max(ms)"].format(sm["max(ms)"])),
        ("Hit@5", SUMMARY_FMT["Hit@5"].format(sm["Hit@5"])),
    ]


def _fmt_delta(metric: str, now: float, base: float | None) -> str:
    """基线对比 Δ：Hit@5 出百分点差（pt），其余出同单位绝对差；基线缺失标'-'。"""
    if base is None:
        return "-"
    delta = now - base
    if metric == "Hit@5":
        return f"{delta * 100:+.1f}pt"
    return f"{delta:+.2f}" if metric == "入库吞吐(docs/s)" else f"{delta:+.1f}"


# -- 报告 ----------------------------------------------------------------------

def _scales_label(scales) -> str:
    """规模点标签：兼容 argparse 原始逗号串与已解析列表（避免逐字符 join）。"""
    if isinstance(scales, str):
        scales = parse_scales(scales)
    return ", ".join(str(s) for s in scales)


def render_report(args, results: list[ScaleResult], baseline: dict | None = None) -> str:
    """渲染 Markdown 报告：头部 + 逐规模点明细表 + 跨规模汇总表（--baseline 时逐指标出 Δ 列）。"""
    lines = [
        "# RAG 性能基准报告（8.5.2）",
        "",
        "- 模式：进程内（真实 BGE + Chroma，Stub LLM；合成语料进程内生成，不入仓不落盘）",
        f"- 库：{args.kb}",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 规模点：{_scales_label(args.scales)}（文档数）",
        f"- 查询：{args.queries} 条固定查询集（seed={args.seed}，取自最小规模点语料事实，跨规模点复用）",
    ]
    if getattr(args, "baseline", None):
        lines.append(f"- 基线：{args.baseline}")
    lines.append("")

    for r in results:
        lines += [f"## 规模点 {r.scale}", "", "| 指标 | 值 |", "|---|---|"]
        lines += [f"| {name} | {value} |" for name, value in _scale_rows(r)]
        lines.append("")

    lines += ["## 跨规模汇总", ""]
    if baseline:
        header = ["规模"] + [c for m in SUMMARY_METRICS for c in (m, f"Δ{SUMMARY_SHORT[m]}")]
    else:
        header = ["规模", *SUMMARY_METRICS]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + " --- |" * len(header))
    for r in results:
        vals = summary_metrics(r)
        cells = [str(r.scale)]
        for m in SUMMARY_METRICS:
            cells.append(SUMMARY_FMT[m].format(vals[m]))
            if baseline:
                cells.append(_fmt_delta(m, vals[m], (baseline.get(r.scale) or {}).get(m)))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    notes = []
    for r in results:
        if r.failed_docs:
            notes.append(f"规模 {r.scale}：{r.failed_docs} 份文档入库失败（已从吞吐分子剔除）")
        if r.errors:
            notes.append(f"规模 {r.scale}：{r.errors} 条查询异常（已从延迟样本剔除，Hit@5 记不命中）")
    if notes:
        lines += ["## 备注", "", * [f"- {n}" for n in notes], ""]
    return "\n".join(lines)


def _parse_baseline(path: Path) -> dict[int, dict[str, float]]:
    """解析基线报告的"跨规模汇总"表 → {规模: {指标: 值}}。

    - Hit@5 归一为 0~1 分数（同 rageval._parse_baseline 口径）；
    - 跳过 Δ 列（列名以 Δ 开头）→ 带对比列的报告亦可作基线；
    - 文件不存在 / 无该表 → 返回 {}（调用方提示）。
    """
    out: dict[int, dict[str, float]] = {}
    if not path.exists():
        return out
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines)
                  if ln.strip().startswith("#") and "跨规模汇总" in ln), None)
    if start is None:
        return out
    cols: list[tuple[int, str]] = []
    for ln in lines[start + 1:]:
        s = ln.strip()
        if not s.startswith("|"):
            if cols:
                break   # 表已结束
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cols:
            cols = [(i, c) for i, c in enumerate(cells)
                    if c and c != "规模" and not c.startswith("Δ")]
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):   # 分隔行
            continue
        try:
            scale = int(cells[0])
        except ValueError:
            continue
        vals: dict[str, float] = {}
        for i, name in cols:
            raw = cells[i] if i < len(cells) else ""
            try:
                value = float(raw.rstrip("%").replace(",", ""))
            except ValueError:
                continue
            vals[name] = value / 100.0 if name == "Hit@5" else value
        if vals:
            out[scale] = vals
    return out


# -- 进程内编排（真实 BGE + Chroma，Stub LLM）------------------------------------

class _StubLLM:
    """固定答案（带 [1] 引用），与 rageval 同款：不依赖外网/真 key。"""

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        return "根据知识库上下文，请参考编号 [1] 对应来源。"


def _fresh_kb(deps, kb_id: str, collection: str | None) -> None:
    """每个规模点开测前清空该库（向量集合 + 文档幂等表，等价 8.4 --fresh-kb 级联）。

    不清幂等表会因 content_hash 命中跳过重嵌 → 吞吐与延迟全部失真。
    """
    if collection and deps.vector_store is not None:
        deps.vector_store.drop_collection(collection)
    if deps.documents is not None:
        deps.documents.delete_by_kb(kb_id)


def _run_scale(deps, kb_id: str, collection: str | None,
               docs: list[DocSpec], queries: list[dict], scale: int) -> ScaleResult:
    """单规模点：fresh-kb 清理 → 逐份入库计时 → 暖机（不计入样本）→ 30 条查询逐条计时。"""
    _fresh_kb(deps, kb_id, collection)

    stage_sums: dict[str, int] = {s: 0 for s in STAGE_ORDER}
    chunks = 0
    failed = 0
    t0 = time.perf_counter()
    for idx, doc in enumerate(docs, start=1):
        job = deps.pipeline.ingest(doc.text.encode("utf-8"), doc.filename,
                                   kb_id=kb_id, collection=collection)
        if job.status != "success":
            failed += 1
            print(f"  ⚠️ 入库失败 {doc.filename}: {job.error_code}", file=sys.stderr)
            continue
        chunks += job.chunk_count or 0
        for stage, cost in stage_diffs(job.stage_timings or {}).items():
            stage_sums[stage] += cost
        if idx % 50 == 0:
            print(f"  已入库 {idx}/{len(docs)}")
    ingest_seconds = time.perf_counter() - t0

    # 暖机：模型加载与 BM25 索引构建属装配开销，显式排除（与 rageval 同口径）
    deps.embedding.ready()
    deps.retriever.retrieve("暖机查询", k=1, collection=collection)

    latencies: list[float] = []
    hits = 0
    errors = 0
    for q in queries:
        started = time.perf_counter()
        try:
            answer = deps.orchestration.run(q["query"], top_k=5, collection=collection)
        except Exception as exc:  # noqa: BLE001  单条异常不中断基准
            errors += 1
            print(f"  ⚠️ 查询异常 {q['id']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        latencies.append((time.perf_counter() - started) * 1000)
        names = [s.get("filename", "") for s in (answer.sources or [])[:5]]
        if set(q["expected_sources"]) & set(names):
            hits += 1

    return ScaleResult(scale=scale, docs=len(docs) - failed, chunks=chunks,
                       ingest_seconds=ingest_seconds, stage_ms=stage_sums,
                       latencies_ms=latencies, queries=len(queries), hits=hits,
                       errors=errors, failed_docs=failed)


# -- 入口 ----------------------------------------------------------------------

def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agentic RAG 性能基准（8.5.2，make perf 底层；进程内真实 BGE+Chroma，Stub LLM）")
    parser.add_argument("--scales", default="50,150,300",
                        help="文档数规模点（逗号分隔，默认 50,150,300）")
    parser.add_argument("--queries", type=int, default=30,
                        help="固定查询条数（默认 30，超出最小规模点语料数时截断）")
    parser.add_argument("--seed", type=int, default=42, help="合成语料随机种子（默认 42，可复现）")
    parser.add_argument("--kb", default=DEFAULT_KB, help="基准专用知识库（默认 perf，禁止 default）")
    parser.add_argument("--out", default=None, help="报告路径，默认 reports/perf-{timestamp}.md")
    parser.add_argument("--baseline", default=None, help="基线报告路径（汇总表逐指标出 Δ 列）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    from app.core.config import load_config
    from app.core.registry import wire
    from app.services.kb_store import DEFAULT_KB_ID, KbStore

    args = _parse_args(argv)
    scales = parse_scales(args.scales)
    if args.queries < 1:
        sys.exit("--queries 必须为正整数")
    if args.kb == DEFAULT_KB_ID:
        sys.exit("拒绝在 default 库上跑性能基准（每个规模点会清空该库），请使用 --kb perf 等专用库")

    # 语料一次性生成到最大规模点（前缀一致 → 前 N 份即规模 N 的语料），查询集固定取最小规模点
    corpus = generate_corpus(args.seed, scales[-1])
    base_docs = corpus[: scales[0]]
    n_queries = min(args.queries, len(base_docs))
    if args.queries > len(base_docs):
        print(f"⚠️ --queries={args.queries} 超过最小规模点语料数，截断为 {n_queries}")
    queries = build_query_set(base_docs, n_queries)

    ks = KbStore()
    if not ks.exists(args.kb):
        ks.create(args.kb)                   # 物理集合随基准创建（进程内隔离）
    collection = ks.collection_of(args.kb)
    deps = wire(load_config(), llm=_StubLLM(), kbs=ks)

    results: list[ScaleResult] = []
    for scale in scales:
        print(f"[perf] 规模点 {scale}（文档 {scale} 份 × 查询 {n_queries} 条）")
        r = _run_scale(deps, args.kb, collection, corpus[:scale], queries, scale)
        results.append(r)
        sm = summary_metrics(r)
        print(f"[perf] 规模 {scale}：入库 {r.ingest_seconds:.1f}s"
              f"（{sm['入库吞吐(docs/s)']:.2f} docs/s，chunks {r.chunks}），"
              f"avg {sm['avg(ms)']:.0f}ms，P95 {sm['P95(ms)']:.0f}ms，"
              f"Hit@5 {sm['Hit@5']:.1%}" + (f"，异常 {r.errors} 条" if r.errors else ""))

    baseline = None
    if args.baseline:
        baseline = _parse_baseline(Path(args.baseline))
        if not baseline:
            print(f"⚠️ 基线解析为空（{args.baseline}），报告将不含 Δ 列")

    out = Path(args.out or f"reports/perf-{time.strftime('%Y%m%d-%H%M%S')}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(args, results, baseline), encoding="utf-8")
    print(f"报告已生成：{out}（{len(out.read_text(encoding='utf-8'))} 字节）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
