"""KbStore 单测（DEVELOPMENT.md 13.5）：种子/增删/持久化/拒删 default/slug 规则。"""
from __future__ import annotations

import pytest

from app.services.kb_store import DEFAULT_KB_ID, KbStore, slugify


@pytest.fixture()
def store(tmp_path):
    return KbStore(persist_file=str(tmp_path / "kb.json"))


def test_default_kb_seeded(store):
    assert len(store) == 1
    info = store.get(DEFAULT_KB_ID)
    assert info is not None
    assert info.collection == "kb", "default 库 collection 沿用存量 kb（零迁移）"
    assert info.name == "默认知识库"


def test_slugify_rules():
    assert slugify("员工手册") == ""            # 纯中文 → 空（fallback 用）
    assert slugify("HR Policies") == "hr_policies"
    assert slugify("IT 支持") == "it"           # ASCII 保留，中文丢弃
    assert slugify("  Mixed  CASE ") == "mixed_case"


def test_create_kb_id_slug(store):
    info = store.create("HR Policies", "人事制度")
    assert info.kb_id == "hr_policies"
    assert info.collection == "hr_policies"
    assert store.exists("hr_policies")


def test_create_chinese_name_falls_back_with_hash(store):
    info = store.create("报销制度")
    assert info.kb_id.startswith("kb_") or info.kb_id != "报销制度"
    assert len(info.kb_id) <= 32
    assert store.exists(info.kb_id)


def test_create_conflict_appends_hash(store):
    store.create("HR Policies")
    second = store.create("HR Policies")
    assert second.kb_id != "hr_policies"
    assert second.kb_id.startswith("hr_policies_")
    assert len(store) == 3


def test_create_empty_name_rejected(store):
    with pytest.raises(ValueError):
        store.create("   ")


def test_delete_non_default(store):
    info = store.create("docs")
    assert store.delete(info.kb_id) is True
    assert not store.exists(info.kb_id)
    assert store.delete("no_such_kb") is False


def test_delete_default_rejected(store):
    assert store.delete(DEFAULT_KB_ID) is False
    assert store.exists(DEFAULT_KB_ID)


def test_collection_of(store):
    assert store.collection_of(DEFAULT_KB_ID) == "kb"
    info = store.create("docs")
    assert store.collection_of("docs") == "docs"
    assert store.collection_of("nope") is None


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "kb.json"
    s1 = KbStore(persist_file=str(path))
    s1.create("HR Policies")
    s1.create("报销制度")
    s2 = KbStore(persist_file=str(path))       # 重载
    assert s2.exists("hr_policies")
    assert s2.exists(DEFAULT_KB_ID)
    assert len(s2) == 3
    assert s2.get("hr_policies").description == ""


def test_persistence_corrupt_falls_back(tmp_path):
    path = tmp_path / "kb.json"
    path.write_text("not json{{{", encoding="utf-8")
    s = KbStore(persist_file=str(path))        # 不崩溃，回退内置 default
    assert s.exists(DEFAULT_KB_ID)
    assert len(s) == 1


def test_event_hooks_called(store):
    events = []
    store.on_kb_changed.append(lambda event, kb_id: events.append((event, kb_id)))
    info = store.create("docs")
    store.delete(info.kb_id)
    assert ("create", "docs") in events
    assert ("delete", "docs") in events


def test_list_contains_default_first(store):
    store.create("hr_policies")
    kbs = store.list()
    assert kbs[0].kb_id == DEFAULT_KB_ID
    assert {k.kb_id for k in kbs} == {DEFAULT_KB_ID, "hr_policies"}
