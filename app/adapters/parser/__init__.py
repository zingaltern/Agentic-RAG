"""文档解析包（DEVELOPMENT.md 5.1、第 16 章）。

职责边界：**只做解析与统一化**（原始字节 → `Document`），不做清洗/重构
（清洗归 `app/adapters/cleaner/`，分块归 `app/adapters/chunker/`）。

对外 API（保持稳定，供 `services/ingestion.py` 与测试引用）：
- `parse_file(file_stream, filename, config=None) -> Document`：解析入口；
- `evaluate_parse_quality` / `reindex_tables`：解析质量检查与表格元数据对齐；
- `SUPPORTED_EXTENSIONS` / `CODE_EXTENSIONS` / `CODE_LANGUAGES` / `TABULAR_EXTENSIONS`：格式常量；
- `PARSER_REGISTRY` / `PARSERS` / `ParserSpec` / `_register_parser`：注册表（P3-8 降级链）。

模块划分：
    formats.py         格式常量（零依赖）
    common.py          统一化层：Document 产出 + 文本解码
    security.py        解析前预检：魔数校验 / zip 炸弹防御
    registry.py        注册表 + 降级链路由
    dispatcher.py      parse_file 分发入口
    quality.py         解析质量检查 + 表格重对齐
    pdf.py             PDF（含表格一等公民）
    office.py          docx / xlsx / pptx + 旧版 xls / doc / ppt
    office_convert.py  LibreOffice 外部转换（旧版 .doc/.ppt）
    tabular.py         CSV / TSV
    text.py            txt / md / html / eml
    json_doc.py        JSON / JSONL（路径扁平化）
    code.py            代码（tree-sitter AST 符号提取 + 纯文本回退）
    ocr.py             OCR 预留接口
"""
from __future__ import annotations

from app.adapters.parser.code import language_extensions, make_code_parser
from app.adapters.parser.common import _build_document, _decode_text
from app.adapters.parser.dispatcher import parse_file
from app.adapters.parser.formats import (
    CODE_EXTENSIONS,
    CODE_LANGUAGES,
    LEGACY_OFFICE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    TABULAR_EXTENSIONS,
)
from app.adapters.parser.json_doc import _parse_json, _parse_jsonl
from app.adapters.parser.ocr import _parse_ocr, _parse_ocr_entry
from app.adapters.parser.office import (
    _parse_doc,
    _parse_docx,
    _parse_ppt,
    _parse_pptx,
    _parse_xls,
    _parse_xlsx,
)
from app.adapters.parser.pdf import _parse_pdf
from app.adapters.parser.quality import evaluate_parse_quality, reindex_tables
from app.adapters.parser.registry import (
    PARSER_REGISTRY,
    PARSERS,
    ParserSpec,
    _register_parser,
)
from app.adapters.parser.security import _check_magic, _check_zip_bomb
from app.adapters.parser.tabular import _parse_csv, _parse_tsv
from app.adapters.parser.text import _parse_eml, _parse_html, _parse_msg, _parse_text

__all__ = [
    "CODE_EXTENSIONS", "CODE_LANGUAGES", "LEGACY_OFFICE_EXTENSIONS",
    "PARSERS", "PARSER_REGISTRY", "ParserSpec", "SUPPORTED_EXTENSIONS",
    "TABULAR_EXTENSIONS", "evaluate_parse_quality", "parse_file", "reindex_tables",
]

# ---- 注册装配（P3-8）：新增格式在此加一行即可 ------------------------------------------

_register_parser(ParserSpec("pymupdf", (".pdf",), _parse_pdf))
_register_parser(ParserSpec("python-docx", (".docx",), _parse_docx))
_register_parser(ParserSpec("openpyxl", (".xlsx",), _parse_xlsx))
_register_parser(ParserSpec("python-pptx", (".pptx",), _parse_pptx))
_register_parser(ParserSpec("native_text", (".md", ".txt"), _parse_text))
_register_parser(ParserSpec("html_std", (".html", ".htm"), _parse_html))
_register_parser(ParserSpec("email_std", (".eml",), _parse_eml))
# V5.2：.msg（Outlook OLE2）不解析——注册为明确报错实现（白名单保留以给出导出 .eml 指引）
_register_parser(ParserSpec("email_msg_unsupported", (".msg",), _parse_msg))
# 16.4：表格类文本（与 xlsx/xls 同口径）
_register_parser(ParserSpec("csv_std", (".csv",), _parse_csv))
_register_parser(ParserSpec("tsv_std", (".tsv",), _parse_tsv))
# 16.3：JSON / JSONL
_register_parser(ParserSpec("json_flatten", (".json",), _parse_json))
_register_parser(ParserSpec("jsonl_flatten", (".jsonl",), _parse_jsonl))
# 16.2：旧版 Office
_register_parser(ParserSpec("xlrd", (".xls",), _parse_xls))
_register_parser(ParserSpec("soffice_doc", (".doc",), _parse_doc))
_register_parser(ParserSpec("soffice_ppt", (".ppt",), _parse_ppt))
# 16.1 / 16.5：代码与配置类（按 grammar 语言分组，未接 grammar 的归入 code_text）
for _language, _exts in language_extensions().items():
    _register_parser(ParserSpec(f"code_{_language}", _exts, make_code_parser(_language)))
# OCR 占位：已注册但不入默认分发表（extensions 为空），供降级链挂载验证
_register_parser(ParserSpec("ocr_dummy", (), _parse_ocr_entry, priority=999))
