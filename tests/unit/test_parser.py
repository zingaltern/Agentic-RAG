"""Parser 契约测试（DEVELOPMENT.md 5.1）：六种格式 + 异常路径。"""
from __future__ import annotations

import hashlib
import io
import re

import pytest

from app.adapters.parser import SUPPORTED_EXTENSIONS, parse_file
from app.core.errors import ParseError

DOC_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def _parse_bytes(raw: bytes, filename: str):
    return parse_file(io.BytesIO(raw), filename)


def test_supported_extensions():
    assert SUPPORTED_EXTENSIONS == {".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt"}


def test_parse_txt():
    doc = _parse_bytes("你好，Agentic RAG。\n第二行。".encode("utf-8"), "手册.txt")
    assert doc.text == "你好，Agentic RAG。\n第二行。"
    assert doc.metadata["source"] == "手册.txt"
    assert doc.metadata["kind"] == "txt"
    assert doc.metadata["page"] is None


def test_parse_md():
    content = "# 员工手册\n\n年假需提前申请。"
    doc = _parse_bytes(content.encode("utf-8"), "员工手册.md")
    assert "员工手册" in doc.text and "年假" in doc.text
    assert doc.metadata["kind"] == "md"


def test_parse_pdf_single_page():
    import fitz

    buf = io.BytesIO()
    pdf = fitz.open()
    page = pdf.new_page()
    # 默认字体（helv）不支持中文，用 ASCII 生成测试 PDF
    page.insert_text((72, 72), "PDF test content 123")
    pdf.save(buf)
    pdf.close()
    doc = _parse_bytes(buf.getvalue(), "说明.pdf")
    assert "PDF test content" in doc.text
    assert doc.metadata["kind"] == "pdf"
    assert doc.metadata["page"] == 1


def test_parse_docx():
    from docx import Document as DocxDocument

    buf = io.BytesIO()
    d = DocxDocument()
    d.add_paragraph("第一段 Word 内容")
    d.add_paragraph("第二段")
    d.save(buf)
    doc = _parse_bytes(buf.getvalue(), "制度.docx")
    assert "第一段 Word 内容" in doc.text
    assert "第二段" in doc.text
    assert doc.metadata["kind"] == "docx"


def test_parse_xlsx():
    from openpyxl import Workbook

    buf = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "员工信息"
    ws.append(["姓名", "部门"])
    ws.append(["张三", "HR"])
    wb.save(buf)
    doc = _parse_bytes(buf.getvalue(), "名单.xlsx")
    assert "姓名" in doc.text and "张三" in doc.text and "HR" in doc.text
    assert doc.metadata["sheets"] == ["员工信息"]
    assert doc.metadata["kind"] == "xlsx"


def test_parse_pptx():
    from pptx import Presentation

    buf = io.BytesIO()
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "产品介绍"
    slide.shapes.add_textbox(0, 0, 100, 100).text_frame.text = "这是 PPT 内容"
    prs.save(buf)
    doc = _parse_bytes(buf.getvalue(), "演示.pptx")
    assert "产品介绍" in doc.text
    assert doc.metadata["kind"] == "pptx"


def test_document_identity_fields():
    raw = "幂等测试内容".encode("utf-8")
    doc = _parse_bytes(raw, "test.txt")
    assert DOC_ID_RE.match(doc.document_id)
    assert doc.content_hash == hashlib.sha256(raw).hexdigest()
    assert doc.title == "test"
    assert doc.filename == "test.txt"


def test_unsupported_extension():
    with pytest.raises(ParseError) as ei:
        _parse_bytes(b"data", "evil.xyz")
    assert ei.value.code == 30003


def test_corrupt_pdf():
    with pytest.raises(ParseError):
        _parse_bytes(b"this is not a real pdf", "broken.pdf")


def test_corrupt_docx():
    with pytest.raises(ParseError):
        _parse_bytes(b"not a zip", "broken.docx")


def test_corrupt_xlsx():
    with pytest.raises(ParseError):
        _parse_bytes(b"not a zip", "broken.xlsx")


def test_corrupt_pptx():
    with pytest.raises(ParseError):
        _parse_bytes(b"not a zip", "broken.pptx")


def test_empty_file_ok():
    doc = _parse_bytes(b"", "empty.txt")
    assert doc.text == ""
