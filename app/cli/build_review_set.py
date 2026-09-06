"""外部基准测试集构建器（DEVELOPMENT.md 8.4）：公开数据集 → 语料文档 + qa_pairs 测试对。

首个数据集：CMRC 2018（中文维基百科抽取式 QA，许可 CC BY-SA 4.0）
来源：https://github.com/ymcui/cmrc2018（哈工大讯飞联合实验室）。
原始 dev 集为扁平结构（[{context_id, context_text, qas:[{query_text, answers:[str]}], title}]），
转换器同时兼容 SQuAD 信封格式（{"data":[{title, paragraphs:[{context, qas}]}]}）。

两层评测策略中的「测试集」：与自建验证集（tests/eval/，make eval）物理分离，
产物落 tests/review/（语料 sample_data/cmrc/ + 用例 qa_pairs/cmrc/qa_pairs.jsonl），
由 `make review`（rageval --kb cmrc --fresh-kb）评测，报告 reports/review-{ts}.md。

语料组织：每 contexts_per_doc 个段落聚合为一份多段落文档（贴近真实多主题文档，
避免一题一文导致检索退化为平凡任务）；direct 期望来源即该聚合文档文件名。

测试对结构（判据与 8.3/13.6 单一实现一致）：
- direct：数据集原始问题 + expected_sources（含答案语料的文档名）→ 判 Hit@1/Hit@5/C-Prec/C-Rec；
- no_answer：取自「未入库段落」的问题（域外追问）→ 判 21001 / 未找到文案兜底（不编造）。

用法：python -m app.cli.build_review_set（原始文件缺失时自动下载；固定 --seed 可复现；
纯标准库，不新增第三方依赖）。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

RAW_URL = "https://raw.githubusercontent.com/ymcui/cmrc2018/master/data/cmrc2018_dev.json"
DEFAULT_RAW = Path("tests/review/raw/cmrc2018_dev.json")
DEFAULT_DOCS_DIR = Path("tests/review/sample_data/cmrc")
DEFAULT_PAIRS = Path("tests/review/qa_pairs/cmrc/qa_pairs.jsonl")

# 抽样约束：段落体量贴近真实文档片段（排除过短/超长），问题最小长度过滤碎题
MIN_CTX_CHARS = 200
MAX_CTX_CHARS = 4000
MIN_QUERY_CHARS = 6
MAX_FILENAME_TITLE = 20

_FS_ILLEGAL = re.compile(r"[/\\:*?\"<>|\s]+")


def fetch_raw(url: str, dest: Path, timeout: float = 60.0) -> Path:
    """下载原始数据集（外部 I/O：超时 + 异常包装为可读错误后退出）。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"数据集下载失败（{url}）：{exc}")
    dest.write_bytes(data)
    return dest


def sanitize_title(title: str) -> str:
    """标题 → 文件名安全片段：去文件系统非法字符与空白。"""
    return _FS_ILLEGAL.sub("_", title.strip()).strip("_") or "untitled"


def _normalize_qa(qa: dict) -> dict | None:
    """兼容两种答案形态：SQuAD（[{"text": ...}]）/ CMRC 2018（["答案文本", ...]）。"""
    question = (qa.get("question") or qa.get("query_text") or "").strip()
    answers = qa.get("answers") or []
    answer = ""
    if answers:
        first = answers[0]
        answer = ((first.get("text") if isinstance(first, dict) else first) or "").strip()
    if question and answer and len(question) >= MIN_QUERY_CHARS:
        return {"question": question, "answer": answer}
    return None


def _flatten(raw: dict | list) -> list[dict]:
    """摊平为段落单元 [{title, context, qas:[{question, answer}]}]，问题已去重、仅保留可抽取题。"""
    units: list[dict] = []
    if isinstance(raw, list):                                   # CMRC 2018 扁平结构
        for item in raw:
            title = (item.get("title") or "").strip()
            context = (item.get("context_text") or "").strip()
            if not title or not context:
                continue
            qas: list[dict] = []
            seen: set[str] = set()
            for qa in item.get("qas", []):
                norm = _normalize_qa(qa)
                if norm and norm["answer"] in context and norm["question"] not in seen:
                    seen.add(norm["question"])
                    qas.append(norm)
            units.append({"title": title, "context": context, "qas": qas})
        return units
    for art in raw.get("data", []):                             # SQuAD 信封结构
        title = (art.get("title") or "").strip()
        for para in art.get("paragraphs", []):
            context = (para.get("context") or "").strip()
            if not title or not context:
                continue
            qas = []
            seen: set[str] = set()
            for qa in para.get("qas", []):
                norm = _normalize_qa(qa)
                if norm and norm["answer"] in context and norm["question"] not in seen:
                    seen.add(norm["question"])
                    qas.append(norm)
            units.append({"title": title, "context": context, "qas": qas})
    return units


def build_review_set(raw: dict | list, num_docs: int = 30, contexts_per_doc: int = 5,
                     qa_per_ctx: int = 1, num_oov: int = 10, seed: int = 42
                     ) -> tuple[list[dict], list[dict]]:
    """确定性生成（语料文档, 测试对）：纯函数不触盘，同 seed 输入必同输出。

    - 语料：每 contexts_per_doc 个段落聚合为一份 .txt 多段落文档（首段标题做文件名与文件头）；
    - direct：每个段落取 ≤qa_per_ctx 条可抽取问题，expected_sources = 所在聚合文档名；
    - no_answer：从未入选段落抽问（域外追问），且问题与答案文本均不出现在任何语料中
      （防"换库同域"误判），数量 ≤num_oov。
    """
    units = [u for u in _flatten(raw)
             if MIN_CTX_CHARS <= len(u["context"]) <= MAX_CTX_CHARS and u["qas"]]
    rng = random.Random(seed)
    rng.shuffle(units)

    per_doc = max(1, contexts_per_doc)
    selected = units[:num_docs * per_doc]
    docs: list[dict] = []
    direct_pairs: list[dict] = []
    used_questions: set[str] = set()
    for idx in range(0, len(selected), per_doc):
        group = selected[idx:idx + per_doc]
        filename = f"cmrc_{idx // per_doc + 1:03d}_{sanitize_title(group[0]['title'])[:MAX_FILENAME_TITLE]}.txt"
        content = "\n\n".join(f"{u['title']}\n\n{u['context']}" for u in group)
        docs.append({"filename": filename, "content": content})
        for unit in group:
            chosen = 0
            for qa in unit["qas"]:
                if chosen >= qa_per_ctx:
                    break
                if qa["question"] in used_questions:
                    continue
                used_questions.add(qa["question"])
                direct_pairs.append({"query": qa["question"], "type": "direct",
                                     "expected_sources": [filename], "variant": "wiki"})
                chosen += 1

    corpus_text = "\n".join(d["content"] for d in docs)
    oov_pairs: list[dict] = []
    for unit in units[num_docs * per_doc:]:
        if len(oov_pairs) >= num_oov:
            break
        for qa in unit["qas"]:
            if qa["question"] in used_questions:
                continue
            if qa["question"] in corpus_text or qa["answer"] in corpus_text:
                continue
            used_questions.add(qa["question"])
            oov_pairs.append({"query": qa["question"], "type": "no_answer",
                              "variant": "oov"})
            break

    pairs = [{"id": f"E{i:03d}", **p} for i, p in enumerate(direct_pairs, 1)]
    pairs += [{"id": f"N{i:03d}", **p} for i, p in enumerate(oov_pairs, 1)]
    return docs, pairs


def write_outputs(docs: list[dict], pairs: list[dict],
                  docs_dir: Path, pairs_path: Path) -> None:
    """落盘：先清理旧语料（换 seed/参数重生成不留脏文件），再写文档与 jsonl。"""
    docs_dir.mkdir(parents=True, exist_ok=True)
    for old in docs_dir.glob("*.txt"):
        old.unlink()
    for d in docs:
        (docs_dir / d["filename"]).write_text(d["content"], encoding="utf-8")
    pairs_path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs)
    pairs_path.write_text(payload + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="外部基准测试集构建器（DEVELOPMENT.md 8.4）")
    parser.add_argument("--url", default=RAW_URL, help="数据集原始 JSON 下载地址")
    parser.add_argument("--raw", default=str(DEFAULT_RAW), help="原始文件保存路径")
    parser.add_argument("--docs-out", default=str(DEFAULT_DOCS_DIR), help="语料文档输出目录")
    parser.add_argument("--pairs-out", default=str(DEFAULT_PAIRS), help="qa_pairs.jsonl 输出路径")
    parser.add_argument("--num-docs", type=int, default=30, help="语料文档（聚合）数")
    parser.add_argument("--contexts-per-doc", type=int, default=5, help="每份文档聚合段落数")
    parser.add_argument("--qa-per-ctx", type=int, default=1, help="每段落最多取问题数")
    parser.add_argument("--num-oov", type=int, default=10, help="域外 no_answer 用例目标数")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（保证可复现）")
    args = parser.parse_args()

    raw_path = Path(args.raw)
    if raw_path.exists():
        print(f"使用已下载的原始文件：{raw_path}（删除后可重新联网下载）")
    else:
        print(f"下载数据集 → {raw_path}")
        fetch_raw(args.url, raw_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    docs, pairs = build_review_set(raw, num_docs=args.num_docs,
                                   contexts_per_doc=args.contexts_per_doc,
                                   qa_per_ctx=args.qa_per_ctx,
                                   num_oov=args.num_oov, seed=args.seed)
    write_outputs(docs, pairs, Path(args.docs_out), Path(args.pairs_out))
    direct = sum(1 for p in pairs if p["type"] == "direct")
    oov = sum(1 for p in pairs if p["type"] == "no_answer")
    print(f"语料文档 {len(docs)} 份 → {args.docs_out}")
    print(f"测试对 {len(pairs)} 条（direct={direct}, no_answer={oov}）→ {args.pairs_out}")
    print(f"评测：make review（基线对比：BASELINE=reports/review-<基线>.md）"
          f"[{time.strftime('%Y-%m-%d %H:%M:%S')}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
