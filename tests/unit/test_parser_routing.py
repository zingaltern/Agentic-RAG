"""解析器降级链单测（P3-8，DEVELOPMENT.md 5.1）。

覆盖：双实现链降级 / 链尽 ParseError / 链尽 gate reject 30005 / metadata.parser 溯源 /
非法链名 warning+跳过 / 空配置行为不变 / PARSERS 兼容展开 / ocr_dummy 挂载机制。
"""
from __future__ import annotations

import io

import pytest

from app.adapters.parser import (
    CODE_EXTENSIONS,
    PARSER_REGISTRY,
    PARSERS,
    ParserSpec,
    _parse_ocr_entry,
    _register_parser,
    parse_file,
)
from app.core.config import load_config
from app.core.errors import ParseError, QualityGateError


def _bad(raw: bytes, config=None) -> tuple[str, dict]:
    raise ParseError("模拟损坏文件")


def _good(raw: bytes, config=None) -> tuple[str, dict]:
    return "备用实现解析出的正文内容，长度充足且包含足够的字符以通过解析质量检查的最小字符数要求。", {}


def _short(raw: bytes, config=None) -> tuple[str, dict]:
    return "短", {}


@pytest.fixture()
def register_test_specs():
    """注册临时测试实现（链按名引用，扩展名留空不污染 PARSERS）。"""
    specs = [ParserSpec("t_bad", (), _bad), ParserSpec("t_good", (), _good),
             ParserSpec("t_short", (), _short)]
    for spec in specs:
        _register_parser(spec)
    yield specs
    for spec in specs:
        PARSER_REGISTRY.pop(spec.name, None)


def _cfg(routing: dict | None):
    """返回 IngestionConfig（parse_file 的 config 形参即该层级，与 pipeline 传参一致）。"""
    config = load_config()
    if routing is not None:
        config.ingestion.parser_routing = routing
    return config.ingestion


# -- 链式降级 --------------------------------------------------------------------------

def test_dual_chain_degrades_to_next(register_test_specs):
    """首实现抛错 → warning 降级 → 次实现成功，metadata.parser 记录实际实现。"""
    config = _cfg({"pdf": {"chain": ["t_bad", "t_good"]}})
    doc = parse_file(io.BytesIO(b"%PDF-fake"), "f.pdf", config)
    assert doc.text.startswith("备用实现")
    assert doc.metadata["parser"] == "t_good"
    assert doc.metadata["parser_version"] == "1"


def test_chain_exhausted_parse_error(register_test_specs):
    """链内全部实现失败（gate 关闭）→ ParseError 30003。"""
    config = _cfg({"pdf": {"chain": ["t_bad", "t_bad"]}})
    with pytest.raises(ParseError):
        parse_file(io.BytesIO(b"%PDF-fake"), "f.pdf", config)


def test_chain_exhausted_gate_reject_30005(register_test_specs):
    """链内产出空文本 + gate reject → QualityGateError 30005（按 P0-1 mode 处理）。"""
    config = _cfg({"pdf": {"chain": ["t_short"]}})
    config.quality_gate.enabled = True
    config.quality_gate.mode = "reject"
    with pytest.raises(QualityGateError) as exc_info:
        parse_file(io.BytesIO(b"%PDF-fake"), "f.pdf", config)
    assert exc_info.value.code == 30005


def test_illegal_chain_name_skipped(register_test_specs):
    """非法链名 warning 跳过（greeting.rules 同款容错），后续合法实现仍生效。"""
    config = _cfg({"pdf": {"chain": ["不存在的实现", "t_good"]}})
    doc = parse_file(io.BytesIO(b"%PDF-fake"), "f.pdf", config)
    assert doc.metadata["parser"] == "t_good"


# -- 空配置行为不变 ----------------------------------------------------------------------

def test_empty_routing_keeps_legacy_path():
    """默认（无路由）：旧单实现路径，无 parser 溯源元数据（零差异承诺）。"""
    doc = parse_file(io.BytesIO(b"hello world content"), "a.txt", None)
    assert "parser" not in doc.metadata
    with pytest.raises(ParseError):
        parse_file(io.BytesIO(b"not a pdf"), "b.pdf", None)


# -- PARSERS 兼容展开与 ocr_dummy 挂载 ----------------------------------------------------

def test_parsers_compat_expansion_from_registry():
    """PARSERS 由注册表按扩展名展开：既有 10 键 + 第 16 章新格式（代码/JSON/旧版 Office）。"""
    assert set(PARSERS) == ({".pdf", ".docx", ".xlsx", ".pptx",
                             ".md", ".txt", ".html", ".htm", ".eml", ".msg",
                             ".json", ".jsonl", ".csv", ".tsv",
                             ".doc", ".xls", ".ppt"}
                            | set(CODE_EXTENSIONS))
    assert PARSERS[".pdf"].__name__ == "_parse_pdf"
    assert PARSERS[".eml"].__name__ == "_parse_eml"
    assert PARSERS[".py"].__name__ == "_parse_code_python"
    assert PARSERS[".js"].__name__ == "_parse_code_javascript"
    assert PARSERS[".yaml"].__name__ == "_parse_code_text"   # 未接 grammar → 纯文本实现
    assert PARSERS[".xls"].__name__ == "_parse_xls"
    assert PARSERS[".csv"].__name__ == "_parse_csv"


def test_ocr_dummy_mountable_via_chain():
    """ocr_dummy 占位：已注册、不入默认链（PARSERS 无 .png 等键）、可经链挂载（保持抛错）。"""
    assert "ocr_dummy" in PARSER_REGISTRY
    assert PARSER_REGISTRY["ocr_dummy"].extensions == ()
    with pytest.raises(ParseError):
        _parse_ocr_entry(b"%PDF-fake")
    config = _cfg({"pdf": {"chain": ["ocr_dummy"]}})
    with pytest.raises(ParseError):
        parse_file(io.BytesIO(b"%PDF-fake"), "f.pdf", config)
