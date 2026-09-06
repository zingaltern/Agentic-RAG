"""PII 两级化单测（P2-6，DEVELOPMENT.md 5.10）。

覆盖：三种 mode / 类型过滤 / detect 不含原文 / kb 级覆盖优先与显式关闭 /
非法库级配置降级 / 旧开关兼容（仅旧开 / 双开新优先）/ kb.json 旧数据兼容 / config 非法值。
**所有号码均为虚构测试数据，禁止真实个人信息入仓。**
"""
from __future__ import annotations

import pytest

from app.core.config import load_config
from app.core.errors import SystemConfigError
from app.core.registry import wire
from app.services.desensitize import detect_pii, desensitize_text
from app.services.kb_store import KbStore
from tests.unit.fakes import StubEmbedding

# 虚构测试数据（非真实信息）
FAKE_ID = "110101199001011234"
FAKE_PHONE = "13800138000"
FAKE_BANK = "6222020000001234"
TEXT = f"员工 {FAKE_ID}，电话 {FAKE_PHONE}，卡号 {FAKE_BANK}。"


# -- 三种 mode 与类型过滤（纯函数） ------------------------------------------------------

def test_mode_mask_default_matches_legacy():
    out = desensitize_text(TEXT)
    assert FAKE_ID not in out and FAKE_PHONE not in out and FAKE_BANK not in out
    assert out.count("***") >= 3
    assert desensitize_text(TEXT) == desensitize_text(TEXT, mode="mask"), "单参调用与旧行为一致"


def test_mode_replace_uses_type_placeholders():
    out = desensitize_text(TEXT, mode="replace")
    assert "[身份证]" in out and "[手机号]" in out and "[银行卡]" in out
    assert FAKE_ID not in out


def test_mode_delete_removes():
    out = desensitize_text(TEXT, mode="delete")
    assert FAKE_ID not in out and FAKE_PHONE not in out and FAKE_BANK not in out
    assert "[身份证]" not in out and "***" not in out


def test_types_filter_only_phone():
    out = desensitize_text(TEXT, mode="mask", types=["phone"])
    assert FAKE_PHONE not in out, "选中类型被脱敏"
    assert FAKE_ID in out and FAKE_BANK in out, "未选类型保持原文"


# -- detect_pii：不含原文 ----------------------------------------------------------------

def test_detect_pii_never_contains_original_text():
    result = detect_pii(TEXT)
    types = {h["type"] for h in result}
    assert types == {"id_card", "phone", "bank_card"}
    for h in result:
        assert set(h) == {"type", "count", "sample_hash"}, "输出仅允许 type/count/sample_hash"
        assert FAKE_ID not in str(h) and FAKE_PHONE not in str(h) and FAKE_BANK not in str(h)
        assert len(h["sample_hash"]) == 8
    assert detect_pii(TEXT) == result, "sample_hash 确定性（同输入同哈希）"
    assert detect_pii("") == []


def test_detect_consumes_id_before_bank():
    """18 位身份证不得被 bank_card 重复计数（消费顺序 id_card → bank_card）。"""
    result = detect_pii(f"证件号 {FAKE_ID}")
    assert {h["type"] for h in result} == {"id_card"}


# -- wire 级：kb 覆盖 / 显式关闭 / 兼容 ---------------------------------------------------

def _wire(tmp_path, isolated_stores, *, global_pii: dict | None = None,
          legacy_switch: bool = False):
    config = load_config()
    config.vector_store.persist_dir = str(tmp_path / "chroma")
    config.documents.persist_file = str(tmp_path / "documents.json")
    config.kbs.persist_file = str(tmp_path / "kb.json")
    if global_pii is not None:
        config.ingestion.pii.enabled = bool(global_pii.get("enabled"))
        config.ingestion.pii.mode = global_pii.get("mode", "mask")
        config.ingestion.pii.types = list(global_pii.get("types") or
                                          ["id_card", "phone", "bank_card"])
    if legacy_switch:
        config.ingestion.desensitize_enabled = True
    deps = wire(config, embedding=StubEmbedding(dimension=512),
                kbs=isolated_stores["kbs"], users=isolated_stores["users"],
                documents=isolated_stores["documents"], jobs=isolated_stores["jobs"])
    deps.config = config
    return deps


def test_kb_level_replace_overrides_global_off(tmp_path, isolated_stores):
    deps = _wire(tmp_path, isolated_stores, global_pii={"enabled": False})
    kb = deps.kbs.create("pii-kb", pii={"enabled": True, "mode": "replace"})
    coll = deps.kbs.collection_of(kb.kb_id)
    job = deps.pipeline.ingest(TEXT.encode("utf-8"), "p.txt", kb_id=kb.kb_id, collection=coll)
    assert job.status == "success"
    chunks = deps.vector_store.get_all_chunks(coll)
    assert chunks
    joined = "".join(c.text for c in chunks)
    assert "[身份证]" in joined and FAKE_ID not in joined, "kb 级策略覆盖全局关闭"


def test_kb_explicit_disable_beats_global_on(tmp_path, isolated_stores):
    deps = _wire(tmp_path, isolated_stores, global_pii={"enabled": True, "mode": "mask"})
    kb = deps.kbs.create("pii-off-kb", pii={"enabled": False})
    coll = deps.kbs.collection_of(kb.kb_id)
    job = deps.pipeline.ingest(TEXT.encode("utf-8"), "p.txt", kb_id=kb.kb_id, collection=coll)
    assert job.status == "success"
    joined = "".join(c.text for c in deps.vector_store.get_all_chunks(coll))
    assert FAKE_ID in joined, "kb.pii {enabled:false} 显式关闭全局策略"


def test_invalid_kb_policy_degrades_to_noop(tmp_path, isolated_stores):
    deps = _wire(tmp_path, isolated_stores, global_pii={"enabled": True, "mode": "mask"})
    kb = deps.kbs.create("pii-bad-kb", pii={"enabled": True, "mode": "不存在的模式"})
    coll = deps.kbs.collection_of(kb.kb_id)
    job = deps.pipeline.ingest(TEXT.encode("utf-8"), "p.txt", kb_id=kb.kb_id, collection=coll)
    assert job.status == "success", "非法库级配置降级为不处理，不阻塞入库"
    joined = "".join(c.text for c in deps.vector_store.get_all_chunks(coll))
    assert FAKE_ID in joined


def test_pii_types_metadata_tagged(tmp_path, isolated_stores):
    deps = _wire(tmp_path, isolated_stores, global_pii={"enabled": True, "mode": "mask"})
    job = deps.pipeline.ingest(TEXT.encode("utf-8"), "p.txt", kb_id="default")
    assert job.status == "success"
    chunks = deps.vector_store.get_all_chunks()
    assert all(c.metadata.get("pii_types") == ["bank_card", "id_card", "phone"] for c in chunks), \
        "命中类型集合打标（仅类型，无原文）"


def test_legacy_switch_alone_still_masks(tmp_path, isolated_stores):
    """兼容期：仅旧开关开启 → mask + 全类型（现网旧行为）。"""
    deps = _wire(tmp_path, isolated_stores, legacy_switch=True)
    job = deps.pipeline.ingest(TEXT.encode("utf-8"), "p.txt", kb_id="default")
    joined = "".join(c.text for c in deps.vector_store.get_all_chunks())
    assert job.status == "success" and FAKE_ID not in joined and "***" in joined


def test_legacy_and_new_both_on_new_wins(tmp_path, isolated_stores):
    """双开：新逻辑（replace）优先于旧开关（mask）。"""
    deps = _wire(tmp_path, isolated_stores, global_pii={"enabled": True, "mode": "replace"},
                 legacy_switch=True)
    deps.pipeline.ingest(TEXT.encode("utf-8"), "p.txt", kb_id="default")
    joined = "".join(c.text for c in deps.vector_store.get_all_chunks())
    assert "[身份证]" in joined, "双开时新逻辑优先"


# -- kb.json 旧数据兼容 ------------------------------------------------------------------

def test_kb_json_without_pii_field_back_compat(tmp_path):
    """旧 kb.json（无 pii 字段）加载 → info.pii 为 None，不报错。"""
    import json

    persist = tmp_path / "kb.json"
    persist.write_text(json.dumps([{
        "kb_id": "legacy", "name": "旧库", "description": "", "collection": "legacy",
        "owner": "admin", "scope": "shared", "created_at": 0.0,
    }]), encoding="utf-8")
    store = KbStore(persist_file=str(persist))
    info = store.get("legacy")
    assert info is not None and info.pii is None


# -- config 非法值 ------------------------------------------------------------------------

def _write_yaml(tmp_path, pii_block: str) -> str:
    """写一份最小合法 yaml（llm 段必填）并注入 pii 块。"""
    path = tmp_path / "settings.yaml"
    path.write_text(
        "env: dev\n"
        "llm:\n  provider: stub\n  model: m\n"
        "ingestion:\n" + pii_block,
        encoding="utf-8",
    )
    return str(path)


def test_pii_config_invalid_mode_raises(tmp_path, monkeypatch):
    for key in [k for k in __import__("os").environ if k.startswith("RAG_")]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemConfigError):
        load_config(_write_yaml(tmp_path, "  pii:\n    enabled: true\n    mode: 坏值\n"))


def test_pii_config_invalid_types_raises(tmp_path, monkeypatch):
    for key in [k for k in __import__("os").environ if k.startswith("RAG_")]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemConfigError):
        load_config(_write_yaml(tmp_path, "  pii:\n    enabled: true\n    types: [passport]\n"))
