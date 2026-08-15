"""用户存储单测（V5.1）：scrypt 哈希、增删改查、持久化容错。"""
from __future__ import annotations

import pytest

from app.services.user_store import UserStore, hash_password, verify_password


@pytest.fixture()
def store(tmp_path):
    return UserStore(persist_file=str(tmp_path / "users.json"))


def test_hash_verify_roundtrip():
    salt, digest = hash_password("pass123")
    assert verify_password("pass123", salt, digest) is True
    assert verify_password("wrong", salt, digest) is False
    other_salt, other_digest = hash_password("pass123")
    assert salt != other_salt, "salt 必须每用户随机"


def test_create_and_authenticate(store):
    store.create("alice", "alice123", name="Alice", role="admin")
    assert store.authenticate("alice", "alice123") is not None
    assert store.authenticate("alice", "nope") is None
    assert store.authenticate("ghost", "x") is None


def test_create_duplicate_username_rejected(store):
    store.create("alice", "p1")
    with pytest.raises(ValueError, match="已存在"):
        store.create("alice", "p2")


def test_create_invalid_role_rejected(store):
    with pytest.raises(ValueError):
        store.create("bob", "p", role="superuser")


def test_update_role_and_reset_password(store):
    user = store.create("bob", "p1")
    assert store.update_role(user.user_id, "admin").role == "admin"
    with pytest.raises(ValueError):
        store.update_role(user.user_id, "nope")
    assert store.reset_password(user.user_id, "newpass") is True
    assert store.authenticate("bob", "newpass") is not None
    assert store.authenticate("bob", "p1") is None


def test_delete_and_get(store):
    user = store.create("carol", "p")
    assert store.delete(user.user_id) is True
    assert store.get_by_id(user.user_id) is None
    assert store.delete("no_such") is False


def test_list_and_is_empty(store):
    assert store.is_empty() is True
    store.create("a", "p")
    store.create("b", "p")
    assert len(store) == 2
    assert {u.username for u in store.list()} == {"a", "b"}


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "users.json"
    s1 = UserStore(persist_file=str(path))
    s1.create("alice", "pw1", role="admin")
    s2 = UserStore(persist_file=str(path))
    assert s2.authenticate("alice", "pw1") is not None
    assert s2.get_by_username("alice").role == "admin"


def test_corrupt_file_falls_back_empty(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("not json{{{", encoding="utf-8")
    store = UserStore(persist_file=str(path))
    assert store.is_empty() is True
