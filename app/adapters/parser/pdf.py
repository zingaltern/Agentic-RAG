"""PDF 解析（DEVELOPMENT.md 5.1）：pymupdf 逐页提取 + 表格一等公民（P1-4）。"""
from __future__ import annotations

import re

from app.adapters.parser.quality import _table_to_markdown
from app.core.errors import ParseError
from app.core.logging import get_logger

logger = get_logger("parser")


def _normalize_pdf_date(raw: str | None) -> str | None:
    """PDF 日期（D:YYYYMMDD...）→ YYYY-MM-DD；无法解析返回 None。"""
    if not raw:
        return None
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", raw)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None

def _pdf_body_size(pdf) -> float:
    """PDF 主体字号估计：全部 span 字号的中位数（标题字号显著大于主体）。"""
    import statistics

    sizes: list[float] = []
    for page in pdf:
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                sizes.extend(span["size"] for span in line.get("spans", []))
    return statistics.median(sizes) if sizes else 12.0

def _pdf_page_headings(page, body_size: float) -> list[str]:
    """按字号启发式提取页内标题行（字号 ≥ 主体 + 2pt）。"""
    headings: list[str] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            sizes = [span["size"] for span in line.get("spans", [])]
            if not sizes:
                continue
            if max(sizes) >= body_size + 2.0:
                title = "".join(span["text"] for span in line.get("spans", [])).strip()
                if title:
                    headings.append(title)
    return headings

def _parse_pdf(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """PDF：pymupdf 逐页抽取文本，表格经 find_tables 转 Markdown 表格按阅读顺序合并（保真）。"""
    import pymupdf

    try:
        pdf = pymupdf.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise ParseError(f"PDF 解析失败（文件损坏或非 PDF）: {exc}") from exc
    try:
        body_size = _pdf_body_size(pdf)
        parts: list[str] = []
        sections: list[tuple[str, int]] = []
        global_line = 0
        # P0-1 探针：逐页统计（total_chars / pages_with_text），供解析质量门禁识别扫描件
        total_chars = 0
        pages_with_text = 0
        # P1-4：按阅读顺序登记全文档表格（index 在汇合后统一补齐）
        all_tables: list[dict] = []
        # P1-5：页码行号区间 [[start_line, end_line, page], ...]，chunk 按段落起始行查表
        page_spans: list[list[int]] = []
        for page in pdf:
            page_start = global_line
            page_text = _extract_page_with_tables(page, all_tables)
            page_lines = page_text.split("\n")
            for heading in _pdf_page_headings(page, body_size):
                try:
                    line_in_page = page_lines.index(heading)
                except ValueError:
                    continue
                sections.append((heading, global_line + line_in_page))
            global_line += len(page_lines)
            parts.append(page_text)
            page_spans.append([page_start, global_line - 1, page.number + 1])
            total_chars += len(page_text)
            if page_text.strip():
                pages_with_text += 1
        metadata = {
            # 单页文档给出页码；多页文档页码按段归属留待 W2 细化（chunk 级 page 标注）
            "page": 1 if pdf.page_count == 1 else None,
            "author": (pdf.metadata or {}).get("author") or None,
            # P0-1：解析探针（garbled_ratio 由 _build_document 统一补齐）
            "text_stats": {
                "total_chars": total_chars,
                "page_count": pdf.page_count,
                "pages_with_text": pages_with_text,
            },
        }
        if sections:
            metadata["sections"] = sections   # T5：字号启发式章节标题
        if pdf.page_count > 1 and page_spans:
            metadata["page_spans"] = page_spans   # P1-5：多页 PDF 页码行号区间（单页已由 page=1 覆盖）
        if all_tables:
            for i, t in enumerate(all_tables):
                t["index"] = i
            metadata["tables"] = all_tables   # P1-4：顺序对应正文第 N 个 [TABLE] 块
        date = _normalize_pdf_date((pdf.metadata or {}).get("creationDate"))
        if date:
            metadata["date"] = date
        return "\n".join(parts), metadata
    finally:
        pdf.close()

def _extract_page_with_tables(page, tables_out: list | None = None) -> str:
    """按 y 坐标把页内文本块与表格（Markdown 化）合并为阅读顺序的文本。

    表格块标记为 [TABLE]...[/TABLE]，与正文段落用空行隔开，供后续分块/清洗识别；
    P1-4：tables_out 非空时按阅读顺序登记 {header, rows, page}（index 由调用方统一补）。
    """
    blocks: list[tuple[float, str]] = []
    raw_tables: list[tuple[float, list[list[str]]]] = []
    # 文本块（dict 模式带坐标）
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = " ".join(
            span["text"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ).strip()
        if text:
            blocks.append((block["bbox"][1], text))  # y0
    # 表格块
    try:
        tables = page.find_tables()
        for table in tables:
            rows = _table_rows(table)
            md = _rows_to_markdown(rows)
            if md:
                blocks.append((table.bbox[1], f"[TABLE]\n{md}\n[/TABLE]"))
                raw_tables.append((table.bbox[1], rows))
    except Exception:
        pass  # 表格抽取失败不阻断正文
    blocks.sort(key=lambda b: b[0])
    if tables_out is not None:
        for _y, rows in sorted(raw_tables, key=lambda t: t[0]):
            tables_out.append({"header": rows[0], "rows": rows[1:],
                               "page": getattr(page, "number", None)})
    return "\n".join(text for _, text in blocks)
