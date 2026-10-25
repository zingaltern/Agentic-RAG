"""P0-1 解析质量门禁单测（DEVELOPMENT.md 5.1/5.10）。

覆盖：parser 探针（text_stats/garbled_ratio）、可复用纯函数 evaluate_parse_quality、
ingestion 门禁（reject→Job failed 30005 / flag 打标放行 / 关闭回归）、
mode 非法值启动即抛 SystemConfigError。
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from app.adapters.parser import evaluate_parse_quality, parse_file
from app.core.config import QualityGateConfig, load_config
from app.core.errors import ParseError, QualityGateError, SystemConfigError
from app.core.registry import wire
from tests.unit.fakes import StubEmbedding

SAMPLE_MD = Path(__file__).resolve().parent.parent / "eval" / "sample_data" / "员工手册.md"


def _make_pdf(page_texts: list[str]) -> bytes:
    """fitz 真实构造多页 PDF（页文本为空串 → 扫描件式空页；helv 字体仅支持 ASCII）。"""
    import fitz

    buf = io.BytesIO()
    pdf = fitz.open()
    for text in page_texts:
        page = pdf.new_page()
        if text:
            page.insert_text((72, 72), text)
    pdf.save(buf)
    pdf.close()
    return buf.getvalue()


def _config(tmp_path, *, gate_enabled=False, mode="reject", empty_result="reject"):
    """单测配置：临时 Chroma/持久化文件，门禁参数可注入（_hermetic_env 已屏蔽 .env）。"""
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.ingestion.quality_gate.enabled = gate_enabled
    config.ingestion.quality_gate.mode = mode
    config.ingestion.empty_result = empty_result
    return config


@pytest.fixture()
def make_deps(tmp_path, isolated_stores):
    """装配工厂：真实 Chroma（临时目录）+ 桩嵌入；按用例开关门禁（仿 test_ingestion deps）。"""

    def _make(*, gate_enabled=False, mode="reject", empty_result="reject"):
        config = _config(tmp_path, gate_enabled=gate_enabled, mode=mode,
                         empty_result=empty_result)
        return wire(config, embedding=StubEmbedding(dimension=512),
                    kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                    documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])

    return _make


# -- parser 探针（5.1） -------------------------------------------------------


def test_pdf_probe_text_stats_normal():
    """多页正常 PDF：page_count/pages_with_text/total_chars 逐页统计，无乱码。"""
    doc = parse_file(io.BytesIO(_make_pdf(["Page one content 123", "Page two content 456"])),
                     "normal.pdf")
    stats = doc.metadata["text_stats"]
    assert stats["page_count"] == 2
    assert stats["pages_with_text"] == 2
    assert stats["total_chars"] > 0
    assert stats["garbled_ratio"] == 0.0


def test_pdf_probe_empty_scan_pdf():
    """扫描件式空文本 PDF：全部页无实质文本，total_chars=0 且乱码率为 0.0（无字符）。"""
    doc = parse_file(io.BytesIO(_make_pdf(["", ""])), "scan.pdf")
    stats = doc.metadata["text_stats"]
    assert stats["page_count"] == 2
    assert stats["pages_with_text"] == 0
    assert stats["total_chars"] == 0
    assert stats["garbled_ratio"] == 0.0


def test_non_pdf_probe_garbled_ratio():
    """非 UTF-8 字节 → errors="replace" 产生 U+FFFD；非 PDF 按单页文档补齐 text_stats。"""
    doc = parse_file(io.BytesIO(b"\xff\xfe bad \xd0\xd1 bytes tail"), "garbled.txt")
    stats = doc.metadata["text_stats"]
    assert stats["page_count"] == 1
    assert stats["pages_with_text"] == 1
    assert stats["total_chars"] == len(doc.text)
    assert stats["garbled_ratio"] > 0.0, "替换字符必须计入乱码率"


# -- evaluate_parse_quality 纯函数 --------------------------------------------


def _stats(**overrides):
    base = {"total_chars": 100, "page_count": 1, "pages_with_text": 1, "garbled_ratio": 0.0}
    base.update(overrides)
    return base


def _evaluate(text, stats):
    return evaluate_parse_quality(text, stats, min_text_chars=30,
                                  max_garbled_ratio=0.05, min_pages_with_text_ratio=0.2)


def test_evaluate_pass_on_healthy_document():
    assert _evaluate("x" * 100, _stats()) == []


def test_evaluate_empty_text_flag():
    assert _evaluate("", _stats()) == ["empty_text"]
    assert _evaluate("短文本", _stats()) == ["empty_text"], "少于 min_text_chars 同样判 empty_text"
    assert _evaluate("x" * 30, _stats()) == [], "恰好等于 min_text_chars 视为达标"


def test_evaluate_high_garbled_flag():
    assert _evaluate("x" * 100, _stats(garbled_ratio=0.5)) == ["high_garbled"]
    assert _evaluate("x" * 100, _stats(garbled_ratio=0.05)) == [], "恰好等于阈值视为达标"


def test_evaluate_few_pages_only_for_multipage_pdf():
    """few_pages_with_text 仅对 page_count>1 的多页 PDF 生效。"""
    sparse = _stats(page_count=10, pages_with_text=1)   # 10% < 20%
    assert _evaluate("x" * 100, sparse) == ["few_pages_with_text"]
    enough = _stats(page_count=10, pages_with_text=2)   # 20% 达标
    assert _evaluate("x" * 100, enough) == []
    single = _stats(page_count=1, pages_with_text=0)    # 单页文档不触发该 flag
    assert _evaluate("x" * 100, single) == []


def test_evaluate_combined_flags_ordered():
    flags = _evaluate("", _stats(garbled_ratio=0.9, page_count=10, pages_with_text=1))
    assert flags == ["empty_text", "high_garbled", "few_pages_with_text"]


# -- ingestion 门禁（5.10） ----------------------------------------------------


def test_empty_result_reject_scan_pdf_job_failed_30007(make_deps):
    """V5.2 默认（empty_result=reject）：空文本扫描件（质量门禁关）→ Job failed=30007。

    杜绝"成功入库 0 块"的静默失败：入库必须给出明确信号。
    """
    deps = make_deps(gate_enabled=False, empty_result="reject")
    job = deps.pipeline.ingest(_make_pdf(["", ""]), "scan.pdf")
    assert job.status == "failed"
    assert job.error_code == "30007"
    assert deps.vector_store.count() == 0
    assert deps.documents.count() == 0


def test_empty_result_allow_legacy_scan_pdf_success_zero_chunks(make_deps):
    """empty_result=allow 回归：扫描件维持旧行为（成功入库 0 块），不产生质量拦截。"""
    deps = make_deps(gate_enabled=False, empty_result="allow")
    job = deps.pipeline.ingest(_make_pdf(["", ""]), "scan.pdf")
    assert job.status == "success"
    assert job.chunk_count == 0
    assert job.error_code is None
    assert deps.vector_store.count() == 0


def test_empty_result_reject_empty_txt(make_deps):
    """空 txt（0 字节）同样受 empty_result=reject 约束。"""
    deps = make_deps(gate_enabled=False, empty_result="reject")
    job = deps.pipeline.ingest(b"", "空白.txt")
    assert job.status == "failed" and job.error_code == "30007"


def test_gate_reject_scan_pdf_job_failed_30005(make_deps):
    """reject 模式：空文本扫描件 → Job failed=30005，不产生任何入库副作用。"""
    deps = make_deps(gate_enabled=True, mode="reject")
    job = deps.pipeline.ingest(_make_pdf(["", ""]), "scan.pdf")
    assert job.status == "failed"
    assert job.error_code == "30005"
    assert deps.jobs.get(job.job_id).error_code == "30005"
    assert deps.vector_store.count() == 0, "拒绝入库不得留下向量残留"
    assert deps.documents.count() == 0


def test_gate_reject_normal_document_zero_impact(make_deps):
    """reject 模式下正常文档零影响：md 与多页正常 PDF 均正常入库（含 text_stats 入链验证）。"""
    deps = make_deps(gate_enabled=True, mode="reject")
    job_md = deps.pipeline.ingest(SAMPLE_MD.read_bytes(), "员工手册.md")
    assert job_md.status == "success" and job_md.chunk_count > 0
    job_pdf = deps.pipeline.ingest(_make_pdf(["Page one content 123", "Page two ok 456"]),
                                   "normal.pdf")
    assert job_pdf.status == "success" and job_pdf.chunk_count > 0
    chunks = deps.vector_store.get_all_chunks("kb")
    pdf_chunks = [c for c in chunks if c.metadata.get("kind") == "pdf"]
    assert pdf_chunks, "正常 PDF 必须成功入库（text_stats 不阻断 Chroma 写入）"


def test_gate_flag_mode_marks_metadata_and_ingests(make_deps):
    """flag 模式：打标 metadata[quality_flags] 后继续入库，块级 metadata 可见。"""
    deps = make_deps(gate_enabled=True, mode="flag")
    job = deps.pipeline.ingest(_make_pdf(["Short"]), "short.pdf")   # 5 字符 < 30 → empty_text
    assert job.status == "success"
    assert job.chunk_count > 0
    chunks = deps.vector_store.get_all_chunks("kb")
    flagged = [c for c in chunks if c.metadata.get("kind") == "pdf"]
    assert flagged and flagged[0].metadata["quality_flags"] == ["empty_text"]


def test_gate_error_type_and_code():
    """QualityGateError：code=30005（区别于 ParseError 30003）、默认文案。"""
    assert QualityGateError.code == 30005
    assert QualityGateError.code != ParseError.code
    assert "扫描件" in QualityGateError().message


# -- 配置校验（4.3：非法值启动即错） --------------------------------------------


def test_config_invalid_mode_raises_system_config_error():
    """mode 非法值：field_validator 抛 SystemConfigError（pydantic v2 原样上抛）。"""
    with pytest.raises(SystemConfigError):
        QualityGateConfig(mode="bogus")


def test_config_invalid_mode_via_env_fails_at_load(monkeypatch):
    """mode 非法值经环境变量覆盖 → load_config 启动即抛 SystemConfigError。"""
    monkeypatch.setenv("RAG_INGESTION_QUALITY_GATE_MODE", "bogus")
    with pytest.raises(SystemConfigError):
        load_config()


def test_config_defaults_match_spec():
    """规格锚点：默认关闭、mode=reject、阈值与 DEVELOPMENT 5.10 一致。"""
    config = load_config()
    gate = config.ingestion.quality_gate
    assert gate.enabled is False
    assert gate.mode == "reject"
    assert gate.min_text_chars == 30
    assert gate.max_garbled_ratio == 0.05
    assert gate.min_pages_with_text_ratio == 0.2
