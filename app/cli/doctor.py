"""一致性巡检 CLI（V5.2，DEVELOPMENT.md 5.10）：documents.json ↔ Chroma ↔ 归档 三方核对。

用法：
    python -m app.cli.doctor [--kb <kb_id>] [--fix]

巡检项（默认只读，`--fix` 仅执行安全修复）：
1. 幽灵文档：索引有条目但集合 0 块（重启/异常中断遗留）→ --fix：删除该条目；
2. 孤儿向量：集合有块但索引无对应条目（或归属其他库）→ --fix：删除这些块；
3. 幂等映射错位：content_hash 映射指向不存在的条目 → --fix：清理映射；
4. 归档缺失：登记 archive_path 但文件不存在 → 仅报告（不自动改索引）。

退出码：0=无问题；1=发现未修复问题（--fix 后仍存在的项）。
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path


def run_doctor(kb_id: str | None = None, fix: bool = False, deps=None) -> dict:
    """执行巡检（deps 可注入供测试）；返回报告 dict。"""
    if deps is None:
        from app.core.config import load_config
        from app.core.registry import wire

        deps = wire(load_config())

    kbs = [kb_id] if kb_id else [k.kb_id for k in deps.kbs.list()]
    report: dict = {
        "checked_kbs": [],
        "ghost_entries": [],      # documents 条目无向量块
        "orphan_chunks": [],      # 向量块无 documents 条目
        "hash_mismatch": [],      # 幂等映射指向不存在条目
        "archive_missing": [],    # 登记归档但文件缺失
        "fixed": {"ghost_entries": 0, "orphan_chunks": 0, "hash_mismatch": 0},
    }
    for kb in kbs:
        info = deps.kbs.get(kb)
        if info is None:
            print(f"[skip] 知识库不存在：{kb!r}")
            continue
        collection = info.collection
        report["checked_kbs"].append(kb)
        docs = deps.documents.list(page=1, size=100000, kb_id=kb)
        doc_ids = {d["document_id"] for d in docs}

        chunks = deps.vector_store.get_all_chunks(collection)
        by_doc: dict[str, int] = defaultdict(int)
        for chunk in chunks:
            by_doc[chunk.document_id] += 1

        # 1) 幽灵文档 + 4) 归档缺失
        for d in docs:
            doc_id = d["document_id"]
            entry = deps.documents.get(doc_id)
            if by_doc.get(doc_id, 0) == 0:
                report["ghost_entries"].append({"kb_id": kb, "document_id": doc_id,
                                                "filename": d.get("filename")})
                if fix:
                    deps.documents.delete(doc_id)      # 含归档级联
                    report["fixed"]["ghost_entries"] += 1
            if entry and entry.archive_path and not Path(entry.archive_path).exists():
                report["archive_missing"].append({"kb_id": kb, "document_id": doc_id,
                                                  "filename": entry.filename,
                                                  "archive_path": entry.archive_path})

        # 2) 孤儿向量
        for doc_id in sorted(by_doc):
            entry = deps.documents.get(doc_id)
            if entry is None or entry.kb_id != kb:
                report["orphan_chunks"].append({"kb_id": kb, "document_id": doc_id,
                                                "chunks": by_doc[doc_id]})
                if fix:
                    deps.vector_store.delete_document(doc_id, collection=collection)
                    report["fixed"]["orphan_chunks"] += 1

    # 3) 幂等映射错位（全局）
    bad = deps.documents.audit_hash_map()
    report["hash_mismatch"] = [{"kb_id": kb, "content_hash": h, "document_id": d}
                               for kb, h, d in bad]
    if fix and bad:
        report["fixed"]["hash_mismatch"] = deps.documents.prune_hash_map()

    _print_report(report, fix)
    remaining = (len(report["ghost_entries"]) + len(report["orphan_chunks"])
                 + len(report["hash_mismatch"]) + len(report["archive_missing"]))
    report["ok"] = remaining == 0
    return report


def _print_report(report: dict, fix: bool) -> None:
    print(f"巡检库：{', '.join(report['checked_kbs']) or '（无）'}")
    print(f"  幽灵文档 {len(report['ghost_entries'])} · 孤儿向量 {len(report['orphan_chunks'])}"
          f" · 幂等映射错位 {len(report['hash_mismatch'])} · 归档缺失 {len(report['archive_missing'])}")
    for key in ("ghost_entries", "orphan_chunks", "hash_mismatch", "archive_missing"):
        for item in report[key][:10]:
            print(f"  [{key}] {item}")
    if fix:
        fixed = report["fixed"]
        print(f"已修复：幽灵 {fixed['ghost_entries']} · 孤儿 {fixed['orphan_chunks']}"
              f" · 幂等映射 {fixed['hash_mismatch']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="索引一致性巡检（documents.json ↔ Chroma ↔ 归档）")
    parser.add_argument("--kb", default=None, help="限定单一知识库（缺省巡检全部）")
    parser.add_argument("--fix", action="store_true", help="执行安全修复（幽灵/孤儿/幂等映射）")
    args = parser.parse_args()
    from app.core.logging import setup_logging

    setup_logging()
    report = run_doctor(kb_id=args.kb, fix=args.fix)
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
