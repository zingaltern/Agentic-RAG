"""敏感数据脱敏单测（DEVELOPMENT.md 13.11.1 S5）。"""
from __future__ import annotations

import pytest

from app.services.desensitize import MASK, desensitize_text


def test_mobile_replaced():
    assert desensitize_text("联系 13812345678 处理") == f"联系 {MASK} 处理"


def test_id_card_18_replaced():
    text = "身份证 110101199003076715"
    out = desensitize_text(text)
    assert "110101199003076715" not in out
    assert MASK in out


def test_id_card_18_with_x_replaced():
    out = desensitize_text("ID 11010119900307671X")
    assert "11010119900307671X" not in out


def test_id_card_15_replaced():
    out = desensitize_text("ID 110101900307671")
    assert "110101900307671" not in out


def test_bank_card_replaced():
    out = desensitize_text("卡号 6222021234567890123")
    assert "6222021234567890123" not in out


def test_plain_text_unchanged():
    text = "年假需提前 3 天在 OA 系统申请，最高累计 20 天。"
    assert desensitize_text(text) == text


def test_empty_text():
    assert desensitize_text("") == ""
    assert desensitize_text(None) is None


def test_short_number_untouched():
    assert "工号 12345" == desensitize_text("工号 12345")


def test_ingestion_desensitizes_chunks(tmp_path, isolated_stores):
    stores = isolated_stores
    """集成：开启脱敏后入库 chunk 不含明文手机号。"""
    from app.core.config import load_config
    from app.core.registry import wire
    from tests.unit.fakes import StubEmbedding

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    config.ingestion.desensitize_enabled = True
    deps = wire(config, embedding=StubEmbedding(dimension=512),
                kbs=stores["kbs"], users=stores["users"], documents=stores["documents"],
                jobs=stores["jobs"])
    raw = "客服电话 13812345678，请在工作日联系。".encode("utf-8")
    job = deps.pipeline.ingest(raw, "contact.txt")
    assert job.status == "success"
    chunks = deps.vector_store.get_all_chunks("kb")
    assert chunks and all("13812345678" not in c.text for c in chunks)
    assert any(MASK in c.text for c in chunks)


def test_ingestion_keeps_plain_when_disabled(tmp_path, isolated_stores):
    stores = isolated_stores
    from app.core.config import load_config
    from app.core.registry import wire
    from tests.unit.fakes import StubEmbedding

    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    deps = wire(config, embedding=StubEmbedding(dimension=512),
                kbs=stores["kbs"], users=stores["users"], documents=stores["documents"],
                jobs=stores["jobs"])
    raw = "客服电话 13812345678，请在工作日联系。".encode("utf-8")
    job = deps.pipeline.ingest(raw, "contact.txt")
    assert job.status == "success"
    chunks = deps.vector_store.get_all_chunks("kb")
    assert chunks and any("13812345678" in c.text for c in chunks)


def test_s6_combined_sensitive_sample_all_masked():
    """S6：身份证/手机号/银行卡混合样本全部脱敏。"""
    sample = "员工 110101199003076715 电话 13812345678 卡号 6222021234567890123 登记完成"
    out = desensitize_text(sample)
    for secret in ("110101199003076715", "13812345678", "6222021234567890123"):
        assert secret not in out
    assert out.count(MASK) >= 3
