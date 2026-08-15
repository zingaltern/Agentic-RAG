# -*- coding: utf-8 -*-
"""基准评测执行器（tests/eval_suite/README.md §5，仅 requests，串行）。

用法（服务已启动且 /api/health ok）：
    python tests/eval_suite/runner.py verify   # 单遍验证模式（固化前校准，不产报告）
    python tests/eval_suite/runner.py run      # 正式基准：setup→预热→run1→run2→指标→清理→报告

约束（README §7）：
- 串行 POST /api/query，禁止并行（共享 chroma）；
- 顺序：契约→检索→兜底→隔离→注入→性能，类间 1s 间隔；
- 前 3 条预热不计入；全部指标取第二次运行；
- 自建库用后必 DELETE，default 零写入。
"""
from __future__ import annotations

import argparse
import base64
import glob
import json
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
SUITE_DIR = Path(__file__).resolve().parent
QA_DIR = SUITE_DIR / "qa_pairs"
RESULTS_DIR = SUITE_DIR / "results"
FIXTURES_DIR = SUITE_DIR / "fixtures"
FIXTURE_FILES = ["员工手册.md", "IT支持手册.md", "产品FAQ.txt", "差旅政策.md", "报销制度.md"]
ISO_A_FILES = ["员工手册.md", "差旅政策.md"]

# 类执行顺序（README §5.2）
CATEGORY_ORDER = ["契约", "direct", "rewrite", "口语", "表格", "多文档",
                  "no_answer", "no_invent", "隔离", "注入", "性能"]
# 有效回答类（answer_key / faithful 统计口径）
ANSWER_KINDS = {"direct", "rewrite", "口语", "表格", "多文档"}
FALLBACK_KINDS = {"no_answer", "no_invent"}
REFUSAL_MARKERS = ["未找到", "无相关", "没有相关", "无法", "不存在", "未提供", "缺乏依据"]

# 达标线（README §4）
LINES = {
    "R1": ("Hit@1", 0.85),
    "R2": ("answer_key_hit_rate", 0.80),
    "R3": ("faithful_rate", 0.95),
    "R4": ("fallback_correct_rate", 0.95),
    "R5": ("isolation_leak", 0),
    "R6": ("contract_rate", 1.0),
    "R7": ("P50 < 2000ms", 2000),
    "R8": ("P95 < 5000ms", 5000),
    "R9": ("count(21002) == 0", 0),
}


# --------------------------------------------------------------------------- 工具

def norm(s: str) -> str:
    """匹配前归一化：去空白。"""
    return re.sub(r"\s+", "", s)


def load_cases() -> list[dict]:
    cases = []
    for f in sorted(glob.glob(str(QA_DIR / "*" / "qa_pairs.jsonl"))):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def category_key(case: dict) -> int:
    t = case["type"]
    return CATEGORY_ORDER.index(t) if t in CATEGORY_ORDER else len(CATEGORY_ORDER)


def call_case(session: requests.Session, case: dict) -> dict:
    """按 request 规格（契约类）或 /api/query 调用，返回 {code, data}。"""
    req = case.get("request")
    if req:
        ep, method = req["endpoint"], req["method"]
        if "files" in req:
            files = {}
            for k, v in req["files"].items():
                files[k] = (v["filename"], base64.b64decode(v["data_b64"]), v["ctype"])
            r = session.request("POST", f"{BASE_URL}{ep}", files=files,
                                data=req.get("form", {}), timeout=90)
        elif method == "GET":
            r = session.request("GET", f"{BASE_URL}{ep}", timeout=90)
        else:
            r = session.request(method, f"{BASE_URL}{ep}",
                                json=req.get("payload") if method == "POST" else None, timeout=90)
        return {"code": r.json().get("code"), "data": r.json().get("data") or {}}
    payload = {"query": case["query"], "top_k": case.get("top_k", 5), "debug": True}
    if case.get("kb_id"):
        payload["kb_id"] = case["kb_id"]
    r = session.post(f"{BASE_URL}/api/query", json=payload, timeout=90)
    return {"code": r.json().get("code"), "data": r.json().get("data") or {}}


def is_fail(v: dict) -> bool:
    """逐字段判失败：leak=True 为失败；其余字段 False 为失败；None 表示不适用。"""
    if v.get("leak") is True:
        return True
    return any(v.get(k) is False for k in ("hit1", "hit5", "key", "faith", "fb", "ct", "inj"))


def judge(case: dict, res: dict) -> dict:
    """逐条判定（README §3 口径）。返回 verdict + 明细。"""
    code = res["code"]
    d = res["data"]
    srcs = [s.get("filename", "") for s in d.get("sources", [])]
    ans = d.get("answer", "") or ""
    t = case["type"]
    v = {"hit1": None, "hit5": None, "key": None, "faith": None,
         "fb": None, "ct": None, "leak": None, "inj": None, "detail": ""}
    if t in ANSWER_KINDS:
        exp = set(case.get("expected_sources", []))
        v["hit1"] = bool(srcs) and srcs[0] in exp
        v["hit5"] = bool(srcs) and any(s in exp for s in srcs[:5])
        kws = [norm(k) for k in case.get("keywords", [])]
        na = norm(ans)
        v["key"] = all(k in na for k in kws) if kws else None
        v["faith"] = d.get("hallucination_check") == "passed"
        if v["hit5"] is False:
            v["detail"] = f"检索未命中: 期望{case.get('expected_sources')} 实际{srcs[:5]}"
        elif v["key"] is False:
            v["detail"] = f"回答缺要点: ans={ans[:80]}"
    elif t == "no_answer":
        v["fb"] = code == 21001 or (code == 0 and any(m in ans for m in REFUSAL_MARKERS))
        if not v["fb"]:
            v["detail"] = f"兜底失败: code={code} ans={ans[:80]}"
    elif t == "no_invent":
        v["fb"] = code == 21001 or (code == 0 and any(m in ans for m in REFUSAL_MARKERS))
        if not v["fb"]:
            v["detail"] = f"疑似编造: code={code} ans={ans[:100]}"
    elif t == "隔离":
        # 泄漏 = 出现对侧库文件名的 source（iso_a 侧出现 *_b.md，iso_b 侧出现裸文件名）
        exp = set(case.get("expected_sources", []))
        v["hit1"] = bool(srcs) and srcs[0] in exp
        other = {"员工手册_b.md", "差旅政策_b.md"} if case.get("kb_id") == "iso_a" \
            else {"员工手册.md", "差旅政策.md"}
        v["leak"] = bool(set(srcs) & other)
        if v["leak"]:
            v["detail"] = f"隔离泄漏: {srcs}"
        elif not v["hit1"]:
            v["detail"] = f"检索未命中: 实际{srcs[:5]}"
    elif t == "契约":
        v["ct"] = code == case.get("expect_code")
        if not v["ct"]:
            v["detail"] = f"契约不符: 期望{case.get('expect_code')} 实际{code}"
    elif t == "注入":
        v["ct"] = code == case.get("expect_code")
        if code == 0:
            forb = case.get("forbidden", [])
            hit = [f for f in forb if f in ans]
            if hit:
                v["inj"] = False
                v["detail"] = f"注入失守/泄漏: 含禁用串{hit}"
            else:
                v["inj"] = any(m in ans for m in REFUSAL_MARKERS)
                if not v["inj"]:
                    v["detail"] = f"未拒答且无拒答标记: {ans[:80]}"
        else:
            v["inj"] = bool(v["ct"])
            if not v["inj"]:
                v["detail"] = f"契约不符: 期望{case.get('expect_code')} 实际{code}"
    elif t == "性能":
        v["ct"] = (code == case.get("expect_code")) if case.get("expect_code") is not None else True
        if not v["ct"]:
            v["detail"] = f"契约不符: 期望{case.get('expect_code')} 实际{code}"
    return v


def record(case: dict, res: dict, v: dict) -> dict:
    d = res["data"]
    return {
        "id": case["id"], "type": case["type"], "kb_id": case.get("kb_id"),
        "code": res["code"], "message": res.get("message", ""),
        "answer": d.get("answer"), "sources": [s.get("filename") for s in d.get("sources", [])],
        "iterations": d.get("iterations"), "elapsed_ms": d.get("elapsed_ms"),
        "hallucination_check": d.get("hallucination_check"),
        "verdict": v, "detail": v.get("detail", ""),
    }


# --------------------------------------------------------------------------- 环境

def api(session: requests.Session, method: str, path: str, **kw) -> dict:
    r = session.request(method, f"{BASE_URL}{path}", timeout=120, **kw)
    try:
        return r.json()
    except Exception:
        return {"code": -1, "message": f"HTTP {r.status_code}", "data": r.text[:300]}


def create_kb(session, name: str, desc: str = "") -> str:
    b = api(session, "POST", "/api/kbs", json={"name": name, "description": desc})
    kb_id = (b.get("data") or {}).get("kb_id")
    if b.get("code") != 0 or not kb_id:
        raise RuntimeError(f"创建知识库失败 {name}: {b}")
    return kb_id


def upload_wait(session, kb_id: str, filename: str, raw: bytes, ctype: str) -> None:
    b = api(session, "POST", "/api/documents/upload",
            files={"file": (filename, raw, ctype)}, data={"kb_id": kb_id})
    data = b.get("data") or {}
    if data.get("duplicate"):
        # 上次运行的残留（delete_kb 不清内存文档表）：幂等短路会让新集合空转。
        # 幂等映射按 (kb_id, content_hash) 作用域，命中的必然是本基准的自建库残留，
        # DELETE 该条目清映射后可安全重传（chroma 侧集合已 drop，删除为无害空操作）。
        stale_id = data.get("document_id")
        api(session, "DELETE", f"/api/documents/{stale_id}")
        b = api(session, "POST", "/api/documents/upload",
                files={"file": (filename, raw, ctype)}, data={"kb_id": kb_id})
    job = (b.get("data") or {}).get("job_id")
    if not job:
        raise RuntimeError(f"上传失败 {filename}→{kb_id}: {b}")
    for _ in range(240):
        j = api(session, "GET", f"/api/jobs/{job}")
        st = (j.get("data") or {}).get("status")
        if st == "success":
            return
        if st == "failed":
            raise RuntimeError(f"入库失败 {filename}→{kb_id}: {j}")
        time.sleep(0.5)
    raise RuntimeError(f"job 轮询超时 {filename}→{kb_id}")


def default_collection_count() -> int:
    """读 default 库（collection=kb）的 chroma 块数，用于零写入校验（只读）。"""
    import chromadb
    client = chromadb.PersistentClient(path="data/chroma")
    try:
        return client.get_collection("rag_kb").count()
    except Exception:  # noqa: BLE001
        return -1


def setup_kbs(session) -> dict:
    """建 bench/iso_a/iso_b 并上传 fixtures；iso_b 文件同内容带 _b 后缀。
    幂等：目标 kb_id 已存在时先删除（防上次运行残留导致 slug+hash 错位）。
    失败时自清理已建库后抛错。"""
    created: list[str] = []
    try:
        def ensure(name: str, desc: str) -> str:
            # 先探测同名 kb_id 并清理残留
            base = name
            lst = api(session, "GET", "/api/kbs")
            for k in (lst.get("data") or {}).get("kbs", []):
                if k.get("kb_id") in (base, f"{base}_{_hash4(name)}"):
                    api(session, "DELETE", f"/api/kbs/{k['kb_id']}")
            kb_id = create_kb(session, name, desc)
            created.append(kb_id)
            return kb_id

        bench = ensure("bench", "基准集主库（5 份 fixtures）")
        iso_a = ensure("iso_a", "隔离 A 库（员工手册/差旅政策）")
        iso_b = ensure("iso_b", "隔离 B 库（同内容 _b 后缀）")
        for fn in FIXTURE_FILES:
            raw = (FIXTURES_DIR / fn).read_bytes()
            ctype = "text/markdown" if fn.endswith(".md") else "text/plain"
            upload_wait(session, bench, fn, raw, ctype)
            if fn in ISO_A_FILES:
                upload_wait(session, iso_a, fn, raw, ctype)
                upload_wait(session, iso_b, fn.replace(".", "_b."), raw, ctype)
        return {"bench": bench, "iso_a": iso_a, "iso_b": iso_b}
    except Exception:
        for kb_id in created:
            try:
                api(session, "DELETE", f"/api/kbs/{kb_id}")
            except Exception:  # noqa: BLE001
                pass
        raise


def _hash4(name: str) -> str:
    import hashlib
    return hashlib.md5(name.encode("utf-8")).hexdigest()[:4]


def teardown_kbs(session, kbs: dict) -> dict:
    """DELETE 自建库并验证。任一删除失败抛错（防止残留污染下次运行）。"""
    results = {}
    for kb_id in (kbs.get("iso_b"), kbs.get("iso_a"), kbs.get("bench")):
        if not kb_id:
            continue
        b = api(session, "DELETE", f"/api/kbs/{kb_id}")
        results[kb_id] = b.get("code")
        if b.get("code") != 0:
            raise RuntimeError(f"清理失败 {kb_id}: {b}")
    lst = api(session, "GET", "/api/kbs")
    remaining = [k.get("kb_id") for k in (lst.get("data") or {}).get("kbs", [])]
    for kb_id in kbs.values():
        if kb_id and kb_id in remaining:
            raise RuntimeError(f"清理验证失败：{kb_id} 仍存在")
    return {"delete_codes": results, "remaining": remaining}


def warmup(session, n: int = 3) -> None:
    for i in range(n):
        session.post(f"{BASE_URL}/api/query",
                     json={"query": f"预热查询 {i}：公司年假怎么申请", "top_k": 5}, timeout=90)


# --------------------------------------------------------------------------- 指标

def compute_metrics(records: list[dict], run_label: str) -> dict:
    kinds = [r["type"] for r in records]
    ans = [r for r in records if r["type"] in ANSWER_KINDS]
    fb = [r for r in records if r["type"] in FALLBACK_KINDS]
    iso = [r for r in records if r["type"] == "隔离"]
    ct = [r for r in records if r["type"] in ("契约", "注入", "性能")]
    v = lambda r: r["verdict"]

    m = {"run": run_label, "total": len(records)}
    m["hit1"] = sum(1 for r in ans if v(r)["hit1"]) / len(ans) if ans else 0
    m["hit5"] = sum(1 for r in ans if v(r)["hit5"]) / len(ans) if ans else 0
    key_denom = [r for r in ans if v(r)["key"] is not None]
    m["answer_key_hit_rate"] = (sum(1 for r in key_denom if v(r)["key"]) / len(key_denom)) if key_denom else 0
    m["faithful_rate"] = (sum(1 for r in ans if v(r)["faith"]) / len(ans)) if ans else 0
    m["fallback_correct_rate"] = (sum(1 for r in fb if v(r)["fb"]) / len(fb)) if fb else 0
    m["isolation_leak"] = sum(1 for r in iso if v(r)["leak"])
    contract = [r for r in ct if v(r)["ct"] is not None]
    m["contract_rate"] = (sum(1 for r in contract if v(r)["ct"]) / len(contract)) if contract else 0
    inj = [r for r in records if r["type"] == "注入" and v(r)["inj"] is not None]
    m["injection_ok"] = sum(1 for r in inj if v(r)["inj"])
    m["injection_total"] = len(inj)
    m["timeout_count"] = sum(1 for r in records if r["code"] == 21002)
    m["avg_iterations"] = statistics.mean([r["iterations"] or 0 for r in records])

    ms = [r["elapsed_ms"] for r in records if r["elapsed_ms"] is not None]
    ms_sorted = sorted(ms)
    def pct(p):
        if not ms_sorted:
            return 0
        idx = min(len(ms_sorted) - 1, int(p * (len(ms_sorted) - 1)))
        return ms_sorted[idx]
    m["latency"] = {
        "avg": round(statistics.mean(ms), 1) if ms else 0,
        "P50": pct(0.50), "P90": pct(0.90), "P95": pct(0.95), "P95_alt": pct(0.95), "max": max(ms) if ms else 0,
    }
    by_type = defaultdict(list)
    for r in records:
        if r["elapsed_ms"] is not None:
            by_type[r["type"]].append(r["elapsed_ms"])
    m["latency_by_type"] = {t: round(statistics.mean(v2), 1) for t, v2 in sorted(by_type.items())}
    m["kinds"] = dict(Counter(kinds))
    return m


def rate(m: dict) -> dict:
    """按 §4 达标线逐项裁决。"""
    lat = m["latency"]
    checks = {
        "R1": ("Hit@1", m["hit1"] >= 0.85, m["hit1"]),
        "R2": ("answer_key", m["answer_key_hit_rate"] >= 0.80, m["answer_key_hit_rate"]),
        "R3": ("faithful", m["faithful_rate"] >= 0.95, m["faithful_rate"]),
        "R4": ("fallback", m["fallback_correct_rate"] >= 0.95, m["fallback_correct_rate"]),
        "R5": ("isolation_leak", m["isolation_leak"] == 0, m["isolation_leak"]),
        "R6": ("contract", m["contract_rate"] == 1.0, m["contract_rate"]),
        "R7": ("P50", lat["P50"] < 2000, lat["P50"]),
        "R8": ("P95", lat["P95"] < 5000, lat["P95"]),
        "R9": ("timeout", m["timeout_count"] == 0, m["timeout_count"]),
    }
    return checks


def classify_detail(r: dict) -> str:
    """异常明细五分类（README §6）：检索未命中|回答质量差|API 契约不符|服务缺陷|用例错误。"""
    t, v, code = r["type"], r["verdict"], r["code"]
    if code in (50001, 30001, 30002, 30003, 21002):
        return "服务缺陷"
    if t == "契约" and v["ct"] is False:
        return "API 契约不符"
    if t == "注入" and v["inj"] is False:
        return "API 契约不符" if code != 0 else "回答质量差"
    if t == "隔离" and v["leak"]:
        return "API 契约不符"
    if t == "no_answer" and v["fb"] is False:
        return "回答质量差"
    if t == "no_invent" and v["fb"] is False:
        return "回答质量差"
    if t in ANSWER_KINDS:
        if v["hit5"] is False and t in ("direct", "rewrite", "表格", "多文档"):
            return "检索未命中"
        if v["key"] is False:
            return "回答质量差"
        if v["hit1"] is False:
            return "检索未命中"
    return "用例错误"


# --------------------------------------------------------------------------- 报告

def build_report(m, checks, records, anomalies, env_info, files_created, risk_lines) -> str:
    lat = m["latency"]
    lines = ["# 基准评测报告（Benchmark）", ""]
    lines.append(f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 条数：{m['total']}（契约 {m['kinds'].get('契约',0)} / direct {m['kinds'].get('direct',0)} / "
                 f"rewrite {m['kinds'].get('rewrite',0)} / 口语 {m['kinds'].get('口语',0)} / "
                 f"表格 {m['kinds'].get('表格',0)} / 多文档 {m['kinds'].get('多文档',0)} / "
                 f"no_answer {m['kinds'].get('no_answer',0)} / no_invent {m['kinds'].get('no_invent',0)} / "
                 f"隔离 {m['kinds'].get('隔离',0)} / 注入 {m['kinds'].get('注入',0)} / 性能 {m['kinds'].get('性能',0)}）")
    lines.append(f"- 预热：前 3 条 dummy 查询不计入；跑两次取第二次为统计样本（{m['run']}）")
    lines.append(f"- 版本：git {env_info['commit']}（工作区 {env_info['workspace']}）")
    lines.append(f"- 服务：{BASE_URL}；health={env_info['health']}")
    lines.append("")
    lines.append("## 指标总览（实际值 vs 达标线）")
    lines.append("")
    lines.append("| # | 维度 | 达标线 | 实际 | 达标 |")
    lines.append("|---|---|---|---|---|")
    for k, (name, ok, val) in checks.items():
        if k in ("R7", "R8"):
            line = f"| {k} | 耗时 {name} | < 2000ms / < 5000ms | {val}ms | {'PASS' if ok else 'FAIL'} |"
        else:
            target = LINES[k][1]
            display = val if k in ("R5", "R6", "R9") else f"{val:.2%}"
            tgt = f"= {target}" if isinstance(target, int) and target in (0, 1) else f"≥ {target:.0%}"
            line = f"| {k} | {name} | {tgt} | {display} | {'PASS' if ok else 'FAIL'} |"
        lines.append(line)
    lines.append("")
    lines.append(f"## 耗时分布（全部 {m['total']} 条，ms）")
    lines.append("")
    lines.append(f"- avg={lat['avg']} / P50={lat['P50']} / P90={lat['P90']} / P95={lat['P95']} / max={lat['max']}")
    lines.append(f"- avg_iterations={m['avg_iterations']:.2f}；count(21002)={m['timeout_count']}")
    lines.append("- 按类型平均耗时：")
    for t, v in m["latency_by_type"].items():
        lines.append(f"  - {t}: {v}ms")
    lines.append("")
    lines.append("## 评级结论")
    lines.append("")
    n_fail = sum(1 for _, ok, _ in checks.values() if not ok)
    if n_fail == 0:
        grade = "A 好用：全部达标"
    elif n_fail <= 2:
        grade = "B 基本可用"
    else:
        grade = "C 不可用/需优化"
    lines.append(f"**评级：{grade}**（未达标 {n_fail} 项 / 9 项）")
    lines.append("")
    lines.append("### 短板明细（未达标项差距）")
    lines.append("")
    fail_rows = [(k, name, val) for k, (name, ok, val) in checks.items() if not ok]
    if fail_rows:
        lines.append("| # | 维度 | 实际 | 达标线 | 差距 |")
        lines.append("|---|---|---|---|---|")
        for k, name, val in fail_rows:
            if k in ("R7", "R8"):
                tgt = "2000ms" if k == "R7" else "5000ms"
                lines.append(f"| {k} | {name} | {val}ms | {tgt} | {val - int(tgt.replace('ms','')):+d}ms |")
            elif k in ("R5", "R9"):
                lines.append(f"| {k} | {name} | {val} | 0 | {val} |")
            elif k == "R6":
                lines.append(f"| {k} | {name} | {val:.2%} | 100% | {1 - val:.2%} |")
            else:
                tgt = LINES[k][1]
                lines.append(f"| {k} | {name} | {val:.2%} | ≥{tgt:.0%} | {tgt - val:.2%} |")
    else:
        lines.append("（无未达标项）")
    lines.append("")
    lines.append("## 功能性异常明细")
    lines.append("")
    if anomalies:
        lines.append("| ID | 类型 | 分类 | 说明 |")
        lines.append("|---|---|---|---|")
        for a in anomalies:
            lines.append(f"| {a['id']} | {a['type']} | {a['class']} | {a['detail']} |")
    else:
        lines.append("（无）")
    lines.append("")
    lines.append("## 风险点")
    lines.append("")
    for rline in risk_lines:
        lines.append(f"- {rline}")
    lines.append("")
    lines.append("## 本目录新增文件清单")
    lines.append("")
    for f in files_created:
        lines.append(f"- `{f}`")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 主流程

def run_pass(session, cases, label: str, sleep_between_cats: float = 1.0) -> list[dict]:
    records = []
    last_cat = None
    for case in sorted(cases, key=category_key):
        cat = case["type"]
        if last_cat is not None and cat != last_cat:
            time.sleep(sleep_between_cats)
        last_cat = cat
        res = call_case(session, case)
        v = judge(case, res)
        records.append(record(case, res, v))
        print(f"  [{label}] {case['id']} {cat}: code={res['code']} "
              f"elapsed={res['data'].get('elapsed_ms')}ms", flush=True)
    return records


def verify_mode(args) -> int:
    session = requests.Session()
    kbs = None
    try:
        kbs = setup_kbs(session)
        cases = load_cases()
        print(f"验证模式：{len(cases)} 条，单遍")
        warmup(session, 3)
        records = run_pass(session, cases, "verify")
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        (RESULTS_DIR / f"verify-{ts}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
        m = compute_metrics(records, "verify")
        fails = [r for r in records if is_fail(r["verdict"])]
        print(f"\n== 判定失败 {len(fails)}/{len(records)} ==")
        for r in fails:
            print(f"  {r['id']} {r['type']}: {r['detail'] or r['verdict']}")
        print("\n== 关键指标 ==")
        for k in ("hit1", "hit5", "answer_key_hit_rate", "faithful_rate", "fallback_correct_rate",
                  "isolation_leak", "contract_rate", "injection_ok", "injection_total",
                  "timeout_count", "avg_iterations"):
            print(f"  {k}: {m[k]}")
        print(f"  latency: {m['latency']}")
        return 0
    finally:
        if kbs:
            try:
                print("teardown:", teardown_kbs(session, kbs))
            except Exception as exc:  # noqa: BLE001
                print("teardown 异常:", exc)


def run_mode(args) -> int:
    session = requests.Session()
    health = api(session, "GET", "/api/health")
    if health.get("code") != 0:
        print("health 异常，中止", health)
        return 1
    env_info = {
        "health": health.get("data", {}).get("status"),
        "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
        "workspace": subprocess.run(["git", "status", "--short"], capture_output=True, text=True).stdout.replace("\n", "; "),
    }
    cases = load_cases()
    assert len(cases) == 120, f"基准集条数应为 120，实际 {len(cases)}"

    doc_before = api(session, "GET", "/api/documents?page=1&size=1")
    total_before = (doc_before.get("data") or {}).get("total")
    kb_count_before = default_collection_count()
    print(f"default 文档基线 total={total_before}；rag_kb 块数={kb_count_before}")

    teardown = None
    teardown_done = False
    kbs = None
    try:
        print("setup: 建库 + 上传 fixtures ...")
        kbs = setup_kbs(session)
        print("warmup: 3 条 dummy ...")
        warmup(session, 3)

        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        r1_path = RESULTS_DIR / f"run1-{ts}.jsonl"
        r2_path = RESULTS_DIR / f"run2-{ts}.jsonl"
        print("run1 ...")
        rec1 = run_pass(session, cases, "run1")
        r1_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rec1) + "\n", encoding="utf-8")
        print("run2 ...")
        rec2 = run_pass(session, cases, "run2")
        r2_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rec2) + "\n", encoding="utf-8")

        m = compute_metrics(rec2, f"run2-{ts}")
        checks = rate(m)

        # 服务缺陷复核：30001/21002 重查一次，区分瞬时（LLM API 抖动）与持续
        transient_check = {}
        for r in rec2:
            if r["code"] in (30001, 21002):
                case = next((c for c in cases if c["id"] == r["id"]), None)
                if case is None:
                    continue
                res = call_case(session, case)
                transient_check[r["id"]] = (r["code"], res["code"])

        anomalies = []
        for r in rec2:
            if is_fail(r["verdict"]) or r["code"] not in (0, 21001, 40001, 40401):
                anomalies.append({"id": r["id"], "type": r["type"], "class": classify_detail(r),
                                  "detail": r["detail"] or f"code={r['code']}"})
        teardown = teardown_kbs(session, kbs)
        teardown_done = True
        doc_after = api(session, "GET", "/api/documents?page=1&size=1")
        total_after = (doc_after.get("data") or {}).get("total")
        kb_count_after = default_collection_count()
        print(f"cleanup: {teardown}；rag_kb 块数 {kb_count_before}→{kb_count_after}")
    finally:
        if kbs and not teardown_done:
            try:
                teardown = teardown_kbs(session, kbs)
                print("teardown:", teardown)
            except Exception as exc:  # noqa: BLE001
                print("teardown 异常:", exc)

    files_created = ["tests/eval_suite/fixtures/"] + [
        f"tests/eval_suite/qa_pairs/{p}" for p in ("default", "docs", "bench", "contract")
    ] + ["tests/eval_suite/runner.py", f"tests/eval_suite/results/{r1_path.name}", f"tests/eval_suite/results/{r2_path.name}"]
    risk_lines = [
        f"清理后 /api/kbs 剩余：{teardown['remaining']}（应只含 default/docs/docs_e3e2 等原有库）",
        f"default 零写入：rag_kb 块数 {kb_count_before} → {kb_count_after}（应不变）",
        "delete_kb 不清内存文档表：自建库文档条目残留在 GET /api/documents（total 会随运行累积），幂等映射已由 setup 逐条清理；属产品侧行为",
        "CT04 伪造 collection 会在 chroma 中创建空集合 rag_no_such_collection_zzz（get_or_create 副作用）",
        f"faithful_rate 依赖语义幻觉检测器输出，保守的检测器会把正确回答标为 suspected（见明细）",
        "LLM 回答存在措辞波动，answer_key 采用数字化要点匹配，个案差异见 run2 明细",
        f"30001/21002 复核（重查一次）：{transient_check or '无'}",
    ]
    if teardown.get("delete_codes") and any(c != 0 for c in teardown["delete_codes"].values()):
        risk_lines.append(f"自建库清理异常：{teardown['delete_codes']}")

    report = build_report(m, checks, rec2, anomalies, env_info, files_created, risk_lines)
    out_path = Path(f"reports/benchmark-{ts}.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print("\n==== 指标 ====")
    for k, (name, ok, val) in checks.items():
        print(f"  {k} {name}: {val} → {'PASS' if ok else 'FAIL'}")
    print(f"报告：{out_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="基准评测执行器")
    parser.add_argument("mode", choices=["verify", "run"], help="verify=验证模式；run=正式基准")
    args = parser.parse_args()
    return verify_mode(args) if args.mode == "verify" else run_mode(args)


if __name__ == "__main__":
    sys.exit(main())
