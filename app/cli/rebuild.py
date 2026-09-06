"""索引重建 CLI（P1-3，DEVELOPMENT.md 13.5）。

用法：
    python -m app.cli.rebuild --kb <kb_id> [--yes]

流程（读 P0-2 归档原件 → 临时 collection 完整重建 → 校验 → 切换指针 → 删旧集合）：
  1. 校验目标库存在、归档原件齐全（任一缺失即中止，旧世界不动）；
  2. dna 相同（collection metadata 与当前配置一致）→ 提示无需重建，退出 0；
  3. 读全部归档字节进内存 → 临时 collection（`rag_<id>__rebuild`）走完整入库链重建
     （新 document_id，归档重新生成）；
  4. 校验：新文档数 == 原文档数、临时集合 dna == 当前配置 dna；
  5. `KbStore.update` 切换 collection 指针（元数据级原子）→ 删除旧集合与旧归档。

失败安全：第 3~4 步任一失败自动回滚（指针复原 + 索引快照恢复 + 临时集合清理），
旧集合与旧归档在成功切换前始终不动。**重建期间请暂停该库的上传与查询**（v1 单机语义）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.core.index_dna import compute_index_dna
from app.core.logging import setup_logging


def rebuild_kb(kb_id: str, yes: bool = False, deps=None) -> dict:
    """执行重建（deps 可注入供测试）；返回摘要 dict，失败抛异常/退出非 0。"""
    from app.core.config import load_config
    from app.core.registry import wire

    if deps is None:
        config = load_config()
        deps = wire(config)
    config = deps.config
    vector_store = deps.vector_store

    info = deps.kbs.get(kb_id)
    if info is None:
        raise SystemExit(f"知识库不存在: {kb_id!r}")
    old_collection = info.collection

    # 1) 归档齐全性检查（任一缺失即中止，旧世界不动）
    docs = deps.documents.list(page=1, size=10_000, kb_id=kb_id)
    if not docs:
        raise SystemExit(f"知识库 {kb_id!r} 无文档，无需重建")
    snapshots: list[tuple[str, str, bytes]] = []   # (filename, archive_path, raw)
    missing: list[str] = []
    for d in docs:
        entry = deps.documents.get(d["document_id"])
        if entry is None or not entry.archive_path or not Path(entry.archive_path).exists():
            missing.append(f"{d['document_id']}({d['filename']})")
            continue
        snapshots.append((entry.filename, entry.archive_path,
                          Path(entry.archive_path).read_bytes()))
    if missing:
        raise SystemExit("以下文档缺少归档原件，无法重建（P0-2 归档开启前的存量文档请重新上传）:\n  "
                         + "\n  ".join(missing))

    # 2) dna 相同 → 无需重建
    current = compute_index_dna(config)
    stored = vector_store.get_collection_dna(old_collection) if hasattr(
        vector_store, "get_collection_dna") else None
    if stored == current["dna"]:
        print(f"集合 {old_collection!r} 的 dna 已与当前配置一致（{current['dna']}），无需重建")
        return {"rebuilt": False, "kb_id": kb_id, "dna": current["dna"]}

    if not yes:
        print(f"即将重建知识库 {kb_id!r}：{len(snapshots)} 份文档，{old_collection!r} → "
              f"{old_collection}__rebuild（dna {stored or 'legacy'} → {current['dna']}）")
        print("重建期间请暂停该库的上传与查询。确认请追加 --yes")
        raise SystemExit(1)

    temp = f"{old_collection}__rebuild"
    vector_store.drop_collection(temp)   # 清理上次失败的残留

    # 3) 快照索引 → 临时集合重建
    removed_entries, removed_hash = deps.documents.forget_kb_index(kb_id)
    deps.kbs.update(kb_id, collection=temp)
    try:
        chunk_total = 0
        new_doc_ids: list[str] = []
        for fname, _old_archive, raw in snapshots:
            job = deps.pipeline.ingest(raw, fname, kb_id=kb_id, collection=temp)
            if job.status != "success":
                raise RuntimeError(f"重建入库失败：{fname}（error_code={job.error_code}）")
            chunk_total += job.chunk_count
            new_doc_ids.append(job.document_id)

        # 4) 校验：文档数 + dna
        new_count = deps.documents.count(kb_id)
        if new_count != len(snapshots):
            raise RuntimeError(f"重建后文档数不符：{new_count} != {len(snapshots)}")
        temp_dna = vector_store.get_collection_dna(temp) if hasattr(
            vector_store, "get_collection_dna") else current["dna"]
        if temp_dna != current["dna"]:
            raise RuntimeError(f"临时集合 dna 不符：{temp_dna} != {current['dna']}")
    except Exception as exc:
        # 回滚：指针复原 + 索引快照恢复 + 临时集合清理（旧集合/旧归档未动）
        deps.kbs.update(kb_id, collection=old_collection)
        deps.documents.restore_entries(removed_entries, removed_hash)
        vector_store.drop_collection(temp)
        raise SystemExit(f"重建失败，已回滚：{exc}") from exc

    # 5) 切换完成：删旧集合 + 清理旧归档
    vector_store.drop_collection(old_collection)
    for _fname, old_archive, _raw in snapshots:
        try:
            Path(old_archive).unlink(missing_ok=True)
        except OSError as exc:
            print(f"警告：旧归档清理失败（忽略）：{old_archive}（{exc}）")

    print(f"重建完成：{len(snapshots)} 份文档 / {chunk_total} 块，"
          f"{old_collection!r} → {temp!r}（dna={current['dna']}）；旧集合已删除")
    return {"rebuilt": True, "kb_id": kb_id, "dna": current["dna"],
            "documents": len(snapshots), "chunks": chunk_total,
            "old_collection": old_collection, "new_collection": temp,
            "new_doc_ids": new_doc_ids}


def main() -> None:
    parser = argparse.ArgumentParser(description="索引全量重建（P1-3）：读归档原件按当前配置重建向量索引")
    parser.add_argument("--kb", required=True, help="目标知识库 id")
    parser.add_argument("--yes", action="store_true", help="跳过确认直接执行")
    args = parser.parse_args()
    setup_logging()
    rebuild_kb(args.kb, yes=args.yes)


if __name__ == "__main__":
    main()
