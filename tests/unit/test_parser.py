"""Parser 契约测试（DEVELOPMENT.md 5.1 + 第 16 章）：既有格式 + 代码/JSON/旧版 Office + 异常路径。"""
from __future__ import annotations

import hashlib
import io
import pathlib
import re

import pytest

from app.adapters.parser import CODE_EXTENSIONS, SUPPORTED_EXTENSIONS, parse_file
from app.core.config import load_config
from app.core.errors import ParseError

DOC_ID_RE = re.compile(r"^[0-9a-f]{12}$")

_OLE2_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _parse_bytes(raw: bytes, filename: str):
    return parse_file(io.BytesIO(raw), filename)


def test_supported_extensions():
    assert SUPPORTED_EXTENSIONS == ({".pdf", ".docx", ".xlsx", ".pptx", ".md", ".txt",
                                     ".html", ".htm", ".eml", ".msg", ".json", ".jsonl",
                                     ".csv", ".tsv",
                                     ".doc", ".xls", ".ppt"} | set(CODE_EXTENSIONS))


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


# -- S2：魔数校验 + zip 炸弹防御（V5.1） -------------------------------------------------

class _Cfg:
    zip_max_total_mb = 1          # 用 1MB 上限做小样本验证
    zip_max_entries = 1000


def test_s2_txt_renamed_to_pdf_rejected():
    with pytest.raises(ParseError, match="格式与扩展名不符"):
        _parse_bytes("这不是 PDF".encode("utf-8"), "fake.pdf")


def test_s2_non_zip_office_rejected():
    with pytest.raises(ParseError, match="非 Office zip"):
        _parse_bytes(b"not a zip at all", "fake.docx")


def test_s2_binary_as_text_rejected():
    with pytest.raises(ParseError, match="二进制"):
        _parse_bytes(b"\x00\x01\x02text", "fake.txt")


def test_s2_zip_bomb_total_size_rejected():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"0" * (2 * 1024 * 1024))   # 解压 2MB > 1MB 上限
    raw = buf.getvalue()
    with pytest.raises(ParseError, match="解压总大小"):
        parse_file(io.BytesIO(raw), "bomb.xlsx", config=_Cfg())


def test_s2_zip_entry_count_rejected():
    import io
    import zipfile

    class _Cfg2:
        zip_max_total_mb = 200
        zip_max_entries = 2

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(3):
            zf.writestr(f"f{i}.txt", b"x")
    with pytest.raises(ParseError, match="条目数"):
        parse_file(io.BytesIO(buf.getvalue()), "many.docx", config=_Cfg2())


def test_s2_legit_formats_still_pass():
    """合法六格式在魔数校验下仍通过（docx/xlsx/pptx 走真实 zip）。"""
    import fitz
    from docx import Document as DocxDocument
    from openpyxl import Workbook

    pdf_buf = io.BytesIO()
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "PDF ok")
    pdf.save(pdf_buf)
    pdf.close()
    doc = parse_file(io.BytesIO(pdf_buf.getvalue()), "a.pdf")
    assert doc.metadata["kind"] == "pdf"

    docx_buf = io.BytesIO()
    DocxDocument().add_paragraph("内容").save(docx_buf) if False else None
    d = DocxDocument()
    d.add_paragraph("内容")
    d.save(docx_buf)
    doc = parse_file(io.BytesIO(docx_buf.getvalue()), "b.docx")
    assert "内容" in doc.text

    xlsx_buf = io.BytesIO()
    wb = Workbook()
    wb.active.append(["a"])
    wb.save(xlsx_buf)
    doc = parse_file(io.BytesIO(xlsx_buf.getvalue()), "c.xlsx")
    assert "a" in doc.text


def test_parse_html_extracts_body():
    from app.adapters.parser import parse_file
    import io as _io

    html = ("<html><head><title>t</title><script>var x=1;</script></head><body>"
            "<h1>公司制度</h1><p>年假需提前申请。</p><style>.x{}</style></body></html>").encode()
    doc = parse_file(_io.BytesIO(html), "制度.html")
    assert "公司制度" in doc.text
    assert "年假需提前申请" in doc.text
    assert "var x" not in doc.text, "script 内容应被剔除"
    assert ".x{}" not in doc.text, "style 内容应被剔除"
    assert doc.metadata["kind"] == "html"


def test_parse_eml_extracts_subject_and_body():
    from app.adapters.parser import parse_file
    import io as _io

    eml = "From: boss@corp.com\nTo: hr@corp.com\nSubject: 报销新规\n\n出差报销需发票实报实销。".encode()
    doc = parse_file(_io.BytesIO(eml), "通知.eml")
    assert "报销新规" in doc.text, "主题应进入文本"
    assert "出差报销需发票实报实销" in doc.text
    assert "boss@corp.com" in (doc.metadata.get("author") or "")


def test_parse_eml_html_fallback():
    from app.adapters.parser import parse_file
    import io as _io

    eml = ("From: a@b.c\nSubject: HTML 邮件\nContent-Type: text/html; charset=utf-8\n\n"
           "<html><body><p>会议改期到周三。</p></body></html>").encode()
    doc = parse_file(_io.BytesIO(eml), "会议.eml")
    assert "会议改期到周三" in doc.text


def test_parse_ocr_not_enabled():
    from app.core.errors import ParseError
    import pytest as _pytest

    with _pytest.raises(ParseError, match="OCR 未启用"):
        from app.adapters.parser import _parse_ocr
        _parse_ocr(b"fake-png", ".png")


def test_html_renamed_as_pdf_rejected():
    """S2：魔数校验对新格式同样生效（HTML 冒充 PDF 被拒）。"""
    from app.adapters.parser import parse_file
    from app.core.errors import ParseError
    import io as _io
    import pytest as _pytest

    fake = "<html><body>不是真 PDF</body></html>".encode()
    with _pytest.raises(ParseError, match="非 PDF"):
        parse_file(_io.BytesIO(fake), "伪装.pdf")


# -- 16.1 代码 / 配置类文件 ---------------------------------------------------------------

def test_allowed_extensions_match_supported():
    """16.4：配置白名单与解析器支持集必须一致（防止两处清单漂移）。"""
    assert {f".{e}" for e in load_config().ingestion.allowed_extensions} == SUPPORTED_EXTENSIONS


def test_parse_code_preserves_lines_and_language():
    """原文保真（不重写源码）+ 前置 [CODE_INDEX] 符号索引块 + 语言标识。"""
    src = "def add(a, b):\n    # 中文注释\n    return a + b\n"
    doc = _parse_bytes(src.encode("utf-8"), "utils.py")
    assert doc.text.endswith(src)                 # 源码原样保留（不做清洗/重排）
    assert doc.text.startswith("[CODE_INDEX language=python symbols=1]")
    assert doc.metadata["kind"] == "py"
    assert doc.metadata["language"] == "python"
    assert doc.metadata["symbols"] == [
        {"name": "add", "kind": "function", "line_start": 1, "line_end": 3}]


def test_parse_code_ast_symbols_python_class_method():
    """AST 符号提取：类 + 方法（方法名带容器前缀）+ 行号。"""
    src = "class A:\n    def m(self):\n        pass\n\ndef f():\n    pass\n"
    doc = _parse_bytes(src.encode("utf-8"), "a.py")
    assert doc.metadata["symbols"] == [
        {"name": "A", "kind": "class", "line_start": 1, "line_end": 3},
        {"name": "A.m", "kind": "method", "line_start": 2, "line_end": 3},
        {"name": "f", "kind": "function", "line_start": 5, "line_end": 6},
    ]
    assert doc.metadata["symbol_count"] == 3


def test_parse_code_symbols_disabled_falls_back_plain_text():
    """code_symbols.enabled=false → 纯文本解码（无索引块、无 symbols）。"""
    cfg = load_config().ingestion
    cfg.code_symbols.enabled = False
    doc = parse_file(io.BytesIO(b"def f():\n    pass\n"), "a.py", cfg)
    assert doc.text == "def f():\n    pass\n"
    assert "symbols" not in doc.metadata


def test_parse_code_no_grammar_falls_back_plain_text():
    """未接 grammar 的配置类扩展名 → 纯文本（不报错）。"""
    doc = _parse_bytes(b"server:\n  port: 8000\n", "conf.yaml")
    assert doc.text == "server:\n  port: 8000\n"
    assert doc.metadata["language"] == "yaml"
    assert "symbols" not in doc.metadata


def test_parse_code_max_symbols_truncates():
    cfg = load_config().ingestion
    cfg.code_symbols.max_symbols = 2
    src = b"def a():\n    pass\n\ndef b():\n    pass\n\ndef c():\n    pass\n"
    doc = parse_file(io.BytesIO(src), "many.py", cfg)
    assert doc.metadata["symbol_count"] == 2


def test_parse_code_config_formats_have_language():
    assert _parse_bytes(b"server:\n  port: 8000\n", "app.yaml").metadata["language"] == "yaml"
    assert _parse_bytes(b"[tool]\nname = 'x'\n", "pyproject.toml").metadata["language"] == "toml"
    assert _parse_bytes(b"<root><a>1</a></root>", "conf.xml").metadata["language"] == "xml"


def test_parse_code_gbk_fallback():
    """GBK 编码的中文注释可正确解码（不产生 U+FFFD）。"""
    doc = _parse_bytes("# 中文注释\nprint('你好')\n".encode("gbk"), "gbk_file.py")
    assert "中文注释" in doc.text and "你好" in doc.text
    assert doc.metadata["text_stats"]["garbled_ratio"] == 0.0


def test_parse_code_all_extensions_dispatch():
    for ext in CODE_EXTENSIONS:
        assert ext in SUPPORTED_EXTENSIONS
        doc = _parse_bytes(b"x = 1\n", f"a{ext}")
        assert doc.metadata["kind"] == ext.lstrip(".")


# -- 16.4 CSV / TSV ----------------------------------------------------------------------

def test_parse_csv_rows_and_columns():
    raw = "姓名,部门,工号\n张三,研发部,1001\n李四,市场部,1002\n".encode("utf-8")
    doc = _parse_bytes(raw, "员工.csv")
    assert doc.text == "姓名\t部门\t工号\n张三\t研发部\t1001\n李四\t市场部\t1002"
    assert doc.metadata["columns"] == ["姓名", "部门", "工号"]
    assert doc.metadata["kind"] == "csv"


def test_parse_csv_skips_empty_cells_and_blank_lines():
    raw = "a,b,c\n1,,3\n\n4,5,\n".encode("utf-8")
    doc = _parse_bytes(raw, "t.csv")
    assert doc.text == "a\tb\tc\n1\t3\n4\t5"


def test_parse_csv_quoted_field_with_delimiter():
    raw = 'a,b\n"含,逗号","第二列"\n'.encode("utf-8")
    doc = _parse_bytes(raw, "q.csv")
    assert "含,逗号\t第二列" in doc.text


def test_parse_csv_gbk_encoding():
    raw = "姓名,部门\n张三,研发部\n".encode("gbk")
    doc = _parse_bytes(raw, "gbk.csv")
    assert "张三\t研发部" in doc.text
    assert doc.metadata["text_stats"]["garbled_ratio"] == 0.0


def test_parse_tsv_tab_delimited():
    doc = _parse_bytes(b"a\tb\n1\t2\n", "t.tsv")
    assert doc.text == "a\tb\n1\t2"
    assert doc.metadata["kind"] == "tsv"


def test_parse_csv_empty_file_ok():
    assert _parse_bytes(b"", "empty.csv").text == ""


# -- 16.3 JSON / JSONL -------------------------------------------------------------------

def test_parse_json_flattens_paths():
    raw = ('{"user": {"name": "张三", "tags": ["a", "b"]}, '
           '"n": 3, "ok": true, "none": null}').encode("utf-8")
    doc = _parse_bytes(raw, "data.json")
    assert "user.name: 张三" in doc.text
    assert "user.tags[0]: a" in doc.text and "user.tags[1]: b" in doc.text
    assert "n: 3" in doc.text
    assert "ok: true" in doc.text
    assert "none: null" in doc.text
    assert doc.metadata["kind"] == "json"


def test_parse_json_empty_containers():
    doc = _parse_bytes(b'{"a": {}, "b": []}', "empty.json")
    assert "a: {}" in doc.text and "b: []" in doc.text


def test_parse_json_invalid_raises():
    with pytest.raises(ParseError, match="JSON 解析失败"):
        _parse_bytes(b"{not json", "bad.json")


def test_parse_json_empty_file_ok():
    """空文件沿用既有语义（空文本，不报错）。"""
    assert _parse_bytes(b"", "empty.json").text == ""
    assert _parse_bytes(b"", "empty.jsonl").text == ""


def test_parse_jsonl_prefixes_records_and_counts():
    doc = _parse_bytes(b'{"a": 1}\n\n{"b": 2}\n', "rows.jsonl")
    assert doc.text.splitlines() == ["[1].a: 1", "[2].b: 2"]
    assert doc.metadata["records"] == 2


def test_parse_jsonl_invalid_line_raises():
    with pytest.raises(ParseError, match="第 2 行"):
        _parse_bytes(b'{"a": 1}\nnot-json\n', "bad.jsonl")


def test_json_max_items_truncates():
    cfg = load_config().ingestion
    cfg.json_flatten.max_items = 3
    doc = parse_file(io.BytesIO(b'{"a": 1, "b": 2, "c": 3, "d": 4}'), "big.json", cfg)
    lines = doc.text.splitlines()
    assert lines[:3] == ["a: 1", "b: 2", "c: 3"]
    assert lines[-1] == "…（已截断，共 3 行）"


def test_json_max_depth_placeholder():
    cfg = load_config().ingestion
    cfg.json_flatten.max_depth = 1
    doc = parse_file(io.BytesIO(b'{"a": {"b": {"c": 1}}}'), "deep.json", cfg)
    assert "<max-depth>" in doc.text


# -- 16.2 旧版 Office --------------------------------------------------------------------

def test_parse_xls_fixture():
    """真实 .xls 样本（tests/unit/fixtures/sample.xls）：逐 sheet 逐行 → 制表符文本。"""
    raw = (pathlib.Path(__file__).parent / "fixtures" / "sample.xls").read_bytes()
    doc = _parse_bytes(raw, "员工.xls")
    assert "姓名\t部门\t工号" in doc.text
    assert "张三\t研发部\t1001" in doc.text        # 整数浮点归一为 1001（非 1001.0）
    assert doc.metadata["sheets"] == ["员工", "备注"]
    assert doc.metadata["kind"] == "xls"


def test_parse_xls_bad_magic_rejected():
    with pytest.raises(ParseError, match="非旧版 Office OLE2"):
        _parse_bytes(b"PK\x03\x04not-ole", "fake.xls")


def test_parse_doc_without_soffice_reports_install_hint(monkeypatch):
    """soffice 缺失 → 明确报错 + 安装指引（不静默降级）。"""
    monkeypatch.setattr("app.adapters.parser.office_convert.shutil.which", lambda name: None)
    with pytest.raises(ParseError, match="LibreOffice") as exc_info:
        _parse_bytes(_OLE2_HEADER + b"\x00" * 32, "old.doc")
    assert "brew install" in str(exc_info.value)


def test_parse_doc_disabled_by_config():
    cfg = load_config().ingestion
    cfg.office_convert.enabled = False
    with pytest.raises(ParseError, match="已关闭"):
        parse_file(io.BytesIO(_OLE2_HEADER + b"\x00" * 32), "old.ppt", cfg)


def test_parse_doc_via_soffice_conversion(monkeypatch):
    """monkeypatch 转换器产出真实 docx → 复用 _parse_docx 并写溯源 metadata。"""
    from docx import Document as DocxDocument

    def _fake_run(cmd, capture_output=True, timeout=None, check=False):
        out_dir = pathlib.Path(cmd[cmd.index("--outdir") + 1])
        document = DocxDocument()
        document.add_paragraph("旧版 Word 转换后的正文内容，用于验证复用 docx 解析链路。")
        document.save(str(out_dir / "input.docx"))

        class _Result:
            returncode = 0
            stderr = b""

        return _Result()

    monkeypatch.setattr("app.adapters.parser.office_convert.shutil.which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr("app.adapters.parser.office_convert.subprocess.run", _fake_run)
    doc = _parse_bytes(_OLE2_HEADER + b"\x00" * 32, "old.doc")
    assert "旧版 Word 转换后的正文内容" in doc.text
    assert doc.metadata["parser"] == "soffice_convert"
    assert doc.metadata["converted_from"] == "doc"


def test_parse_doc_soffice_timeout(monkeypatch):
    import subprocess as _subprocess

    def _timeout(cmd, capture_output=True, timeout=None, check=False):
        raise _subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr("app.adapters.parser.office_convert.shutil.which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr("app.adapters.parser.office_convert.subprocess.run", _timeout)
    with pytest.raises(ParseError, match="转换超时"):
        _parse_bytes(_OLE2_HEADER + b"\x00" * 32, "old.doc")


def test_parse_doc_soffice_no_output(monkeypatch):
    """转换进程正常退出但无产物 → ParseError（含 stderr 摘要）。"""

    class _Result:
        returncode = 1
        stderr = b"Error: source file could not be loaded"

    monkeypatch.setattr("app.adapters.parser.office_convert.shutil.which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr("app.adapters.parser.office_convert.subprocess.run",
                        lambda *a, **k: _Result())
    with pytest.raises(ParseError, match="未产出"):
        _parse_bytes(_OLE2_HEADER + b"\x00" * 32, "old.ppt")
