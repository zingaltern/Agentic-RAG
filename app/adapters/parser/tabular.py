"""CSV / TSV 解析（DEVELOPMENT.md 16.4）：与 xlsx/xls 同口径。

只做解析与统一化：统一编码（`_decode_text`）→ 标准库 `csv` 逐行读取 →
每行去空单元格后以制表符连接（空行忽略），与 Excel 解析产出形态一致，便于跨格式检索。
"""
from __future__ import annotations

import csv
import io

from app.adapters.parser.common import _decode_text
from app.core.errors import ParseError

_MAX_ROWS = 200_000        # 行数上限（超大表防内存膨胀，与 max_file_size_mb 配合）


def _parse_delimited(raw: bytes, config: object | None, delimiter: str) -> tuple[str, dict]:
    """按给定分隔符解析表格文本；空文件 → 空文本（沿用既有语义）。"""
    text = _decode_text(raw)
    if not text.strip():
        return "", {"page": None, "author": None}
    try:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    except csv.Error as exc:
        raise ParseError(f"表格文本解析失败（CSV/TSV）: {exc}") from exc

    lines: list[str] = []
    header: list[str] = []
    truncated = False
    for index, row in enumerate(rows):
        if len(lines) >= _MAX_ROWS:
            truncated = True
            break
        cells = [cell.strip() for cell in row]
        cells = [cell for cell in cells if cell]
        if not cells:
            continue
        if index == 0 and not header:
            header = cells
        lines.append("\t".join(cells))
    if truncated:
        lines.append(f"…（已截断，共 {_MAX_ROWS} 行）")

    meta: dict = {"page": None, "author": None}
    if header:
        meta["columns"] = header          # 首行作为列名登记（正文仍保持逐行制表符形态）
    return "\n".join(lines), meta


def _parse_csv(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """CSV（逗号分隔）。"""
    return _parse_delimited(raw, config, ",")


def _parse_tsv(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """TSV（制表符分隔）。"""
    return _parse_delimited(raw, config, "\t")
