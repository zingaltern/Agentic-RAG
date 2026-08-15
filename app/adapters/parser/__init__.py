"""文档解析（DEVELOPMENT.md 5.1）。

契约（3.9）：`parse_file(file_stream: BinaryIO, filename: str) -> Document`
- 支持 .pdf/.docx/.xlsx/.pptx/.md/.txt，text 归一为 UTF-8。
- metadata 保留键：source（原文件名）、kind（扩展名）、page（int/None）、author（str/None）。
- 损坏/不支持 → ParseError（code 30003）。
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.core.domain import Document
from app.core.errors import ParseError

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt"}


def _build_document(filename: str, raw: bytes, text: str, metadata: dict) -> Document:
    """按 2.3 约定生成 document_id（uuid4 hex 前 12 位）与 content_hash（sha256 原始字节）。

    content_hash 写入 metadata：Chunk 继承后可用于幂等去重检索（W2 5.10）。
    """
    content_hash = hashlib.sha256(raw).hexdigest()
    metadata = {
        "source": filename,
        "kind": Path(filename).suffix.lstrip(".").lower(),
        "content_hash": content_hash,
        **metadata,
    }
    return Document(
        document_id=uuid4().hex[:12],
        filename=filename,
        title=Path(filename).stem,
        content_hash=content_hash,
        text=text,
        metadata=metadata,
    )


def _check_magic(ext: str, raw: bytes, filename: str, config: object | None = None) -> None:
    """S2（V5.1）：上传前按魔数校验真实格式与扩展名一致（pdf %PDF；zip 系 PK\x03\x04；txt/md 文本嗅探）。

    不一致抛 ParseError（伪造扩展名/损坏文件统一语义）。
    """
    if ext == ".pdf":
        if not raw.startswith(b"%PDF"):
            raise ParseError(f"文件格式与扩展名不符（非 PDF）: {filename!r}")
    elif ext in (".docx", ".xlsx", ".pptx"):
        if not raw.startswith(b"PK\x03\x04"):
            raise ParseError(f"文件格式与扩展名不符（非 Office zip）: {filename!r}")
        _check_zip_bomb(raw, filename, config)
    else:  # .md / .txt
        sample = raw[:512]
        if sample and b"\x00" in sample:
            raise ParseError(f"文件格式与扩展名不符（疑似二进制冒充文本）: {filename!r}")


def _check_zip_bomb(raw: bytes, filename: str, config: object | None = None) -> None:
    """S2（V5.1）：zip 解压炸弹防御 —— 解压后总大小/条目数上限（默认 200MB/1000 条）。"""
    import io as _io
    import zipfile as _zipfile

    total_mb = int(getattr(config, "zip_max_total_mb", 200)) if config is not None else 200
    max_entries = int(getattr(config, "zip_max_entries", 1000)) if config is not None else 1000
    try:
        with _zipfile.ZipFile(_io.BytesIO(raw)) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ParseError(f"zip 条目数超过上限（{max_entries}）: {filename!r}")
            total = sum(info.file_size for info in infos)
            if total > total_mb * 1024 * 1024:
                raise ParseError(f"zip 解压总大小超过上限（{total_mb}MB）: {filename!r}")
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"zip 校验失败: {filename!r}（{exc}）") from exc


def parse_file(file_stream: BinaryIO, filename: str, config: object | None = None) -> Document:
    """按扩展名分发解析；统一异常包装为 ParseError（5.1）；S2 魔数/zip 炸弹预检。"""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"不支持的文档格式: {filename!r}（支持：{sorted(e for e in SUPPORTED_EXTENSIONS)}）")
    try:
        raw = file_stream.read()
    except Exception as exc:
        raise ParseError(f"读取文件失败: {filename}") from exc
    if not isinstance(raw, bytes):
        raw = bytes(raw)
    _check_magic(ext, raw, filename, config)
    try:
        if ext == ".pdf":
            text, meta = _parse_pdf(raw)
        elif ext == ".docx":
            text, meta = _parse_docx(raw)
        elif ext == ".xlsx":
            text, meta = _parse_xlsx(raw)
        elif ext == ".pptx":
            text, meta = _parse_pptx(raw)
        else:  # .md / .txt
            text, meta = _parse_text(raw)
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"{filename} 解析失败: {exc}") from exc
    return _build_document(filename, raw, text or "", meta)


def _parse_text(raw: bytes) -> tuple[str, dict]:
    """txt/md：按 UTF-8 解码，非 UTF-8 字节以替换字符归一（5.1）。"""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return text, {"page": None, "author": None}


def _parse_pdf(raw: bytes) -> tuple[str, dict]:
    """PDF：pymupdf（fitz）逐页抽取文本。"""
    import fitz

    try:
        pdf = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise ParseError(f"PDF 解析失败（文件损坏或非 PDF）: {exc}") from exc
    try:
        parts = [page.get_text() for page in pdf]
        metadata = {
            # 单页文档给出页码；多页文档页码按段归属留待 W2 细化（chunk 级 page 标注）
            "page": 1 if pdf.page_count == 1 else None,
            "author": (pdf.metadata or {}).get("author") or None,
        }
        return "\n".join(parts), metadata
    finally:
        pdf.close()


def _parse_docx(raw: bytes) -> tuple[str, dict]:
    """Word：python-docx 段落级 + 表格单元格。"""
    from docx import Document as DocxDocument

    try:
        doc = DocxDocument(io.BytesIO(raw))
    except Exception as exc:
        raise ParseError(f"Word 解析失败（文件损坏或非 docx）: {exc}") from exc
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(cells))
    try:
        author = doc.core_properties.author or None
    except Exception:
        author = None
    return "\n".join(lines), {"page": None, "author": author}


def _parse_xlsx(raw: bytes) -> tuple[str, dict]:
    """Excel：每 sheet 每行判为一段文本；metadata 记录 sheet 名。"""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseError(f"Excel 解析失败（文件损坏或非 xlsx）: {exc}") from exc
    try:
        lines: list[str] = []
        sheets: list[str] = []
        for ws in wb.worksheets:
            sheets.append(ws.title)
            for row in ws.iter_rows(values_only=True):
                vals = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if vals:
                    lines.append("\t".join(vals))
        return "\n".join(lines), {"page": None, "author": None, "sheets": sheets}
    finally:
        wb.close()


def _parse_pptx(raw: bytes) -> tuple[str, dict]:
    """PPT：python-pptx 按幻灯片/文本框抽取。"""
    from pptx import Presentation

    try:
        prs = Presentation(io.BytesIO(raw))
    except Exception as exc:
        raise ParseError(f"PPT 解析失败（文件损坏或非 pptx）: {exc}") from exc
    lines: list[str] = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(text)
    return "\n".join(lines), {"page": None, "author": None}
