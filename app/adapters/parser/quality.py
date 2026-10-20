"""解析质量与表格统一化辅助（DEVELOPMENT.md 5.1 P0-1 / P1-4）。

- `evaluate_parse_quality`：纯函数，返回 flag 列表（降级链与质量门禁共用）；
- `reindex_tables`：清洗后按 [TABLE] 块重对齐 metadata["tables"]；
- `_rows_to_markdown` / `_table_to_markdown`：表格渲染工具（PDF / Word 解析器共用）。

不含清洗逻辑（清洗归 `app/adapters/cleaner/`），只做解析事实的检查与对齐。
"""
from __future__ import annotations

import re

from app.core.domain import Document
from app.core.logging import get_logger

logger = get_logger("parser")


def evaluate_parse_quality(text: str, text_stats: dict, *,
                           min_text_chars: int, max_garbled_ratio: float,
                           min_pages_with_text_ratio: float) -> list[str]:
    """P0-1 解析质量检查（模块级纯函数，P3-8 解析器降级链复用）。

    返回 flag 列表（空 = 通过）：
    - empty_text：去首尾空白后文本少于 min_text_chars（含扫描件抽出空文本）；
    - high_garbled：U+FFFD 乱码占比超过 max_garbled_ratio（text_stats 缺
      garbled_ratio 时按当前文本现算兜底）；
    - few_pages_with_text：仅对 page_count>1 的多页 PDF 生效——有实质文本页占比
      pages_with_text/page_count 低于 min_pages_with_text_ratio（疑似扫描件大部分页无文本层）。
    """
    flags: list[str] = []
    stats = text_stats or {}
    if len((text or "").strip()) < min_text_chars:
        flags.append("empty_text")
    ratio = stats.get("garbled_ratio")
    if ratio is None:
        total = len(text or "")
        ratio = (text or "").count("\ufffd") / total if total else 0.0
    if ratio > max_garbled_ratio:
        flags.append("high_garbled")
    page_count = int(stats.get("page_count") or 0)
    pages_with_text = int(stats.get("pages_with_text") or 0)
    if page_count > 1 and (pages_with_text / page_count) < min_pages_with_text_ratio:
        flags.append("few_pages_with_text")
    return flags

_TABLE_BLOCK_RE = re.compile(r"\[TABLE\]\n(.*?)\n\[/TABLE\]", flags=re.S)

def _md_header_cells(block: str) -> list[str]:
    """从 [TABLE] 块提取表头单元格（第一条非分隔行的管道单元格）。"""
    for line in block.split("\n"):
        s = line.strip()
        if not s.startswith("|"):
            continue
        if set(s) <= {"|", "-", " ", ":"}:
            continue   # |---|---| 分隔行
        return [c.strip() for c in s.strip("|").split("|")]
    return []

def reindex_tables(doc: Document) -> None:
    """P1-4：清洗后按 [TABLE] 出现顺序重对齐 metadata["tables"]（DEVELOPMENT 5.1）。

    - 数量一致 → 无操作（normalize 白名单保证表内容未被改写，顺序天然对齐）；
    - cleaner（noise/dedup/quality）删掉了含表格的段落 → 按表头单元格匹配重对齐，
      对不上号的 JSON 丢弃并 warning（表格正文还在 text 中、JSON 缺失时降级为纯文本检索）。
    """
    tables = doc.metadata.get("tables")
    if not tables:
        return
    blocks = _TABLE_BLOCK_RE.findall(doc.text or "")
    if len(blocks) == len(tables):
        return
    kept: list[dict] = []
    used: set[int] = set()
    for block in blocks:
        header = _md_header_cells(block)
        match = next((i for i, t in enumerate(tables)
                      if i not in used
                      and [str(c) for c in (t.get("header") or [])] == header), None)
        if match is None:   # 表头匹配失败（空表头等）→ 取剩余未用表的顺序兜底
            match = next((i for i in range(len(tables)) if i not in used), None)
        if match is None:
            break
        used.add(match)
        kept.append({**tables[match], "index": len(kept)})
    if len(kept) != len(tables):
        logger.warning(
            f"清洗后表格 JSON 重对齐：{len(tables)}→{len(kept)}，"
            f"丢弃 {len(tables) - len(kept)} 个无对应 [TABLE] 块的登记",
            extra={"extra_fields": {"before": len(tables), "after": len(kept),
                                    "filename": doc.filename}},
        )
    doc.metadata["tables"] = kept

def _table_rows(table) -> list[list[str]]:
    """表格 → 清洗后的二维行（P1-4：markdown 与 tables JSON 共用同一份行数据）。"""
    rows = table.extract() or []
    rows = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
    return [row for row in rows if any(row)]

def _rows_to_markdown(rows: list[list[str]]) -> str:
    """行数据 → Markdown 管道表格（首行表头 + 分隔行）。"""
    if not rows:
        return ""
    lines = ["| " + " | ".join(rows[0]) + " |",
             "|" + "|".join("---" for _ in rows[0]) + "|"]
    for row in rows[1:]:
        while len(row) < len(rows[0]):
            row.append("")
        lines.append("| " + " | ".join(row[:len(rows[0])]) + " |")
    return "\n".join(lines)

def _table_to_markdown(table) -> str:
    """表格 → Markdown 管道表格（兼容旧调用；P1-4 起内部走 _table_rows/_rows_to_markdown）。"""
    return _rows_to_markdown(_table_rows(table))
