"""持久化安全工具单测（S7，V5.1）：原子写 + 0o600 最小权限。"""
from __future__ import annotations

import os
import stat

from app.core.persist import atomic_write_json


def test_atomic_write_json_creates_with_0600(tmp_path):
    target = tmp_path / "users.json"
    atomic_write_json(target, [{"a": 1}])
    assert target.exists()
    assert (target.stat().st_mode & 0o777) == 0o600, "落盘权限必须收紧为 0o600"
    import json

    assert json.loads(target.read_text(encoding="utf-8")) == [{"a": 1}]


def test_atomic_write_json_no_tmp_leftovers(tmp_path):
    target = tmp_path / "kb.json"
    atomic_write_json(target, {"x": 1})
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "写入完成后不得残留临时文件"
    assert not any(p.name.startswith(f".{target.name}.") for p in tmp_path.iterdir())


def test_atomic_write_json_overwrites_existing(tmp_path):
    target = tmp_path / "jobs.json"
    atomic_write_json(target, [1])
    atomic_write_json(target, [1, 2])
    import json

    assert json.loads(target.read_text(encoding="utf-8")) == [1, 2]


def test_atomic_write_json_raises_on_bad_payload(tmp_path):
    target = tmp_path / "bad.json"

    class _NonSerializable:
        pass

    try:
        atomic_write_json(target, {"x": _NonSerializable()})
        raised = False
    except (TypeError, ValueError):
        raised = True
    assert raised, "不可序列化 payload 必须抛异常"
    assert not target.exists(), "失败时不得留下半截目标文件"
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == [], "失败时临时文件必须清理"


def test_atomic_write_json_works_with_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    atomic_write_json("data/users.json", [{"u": "a"}])
    assert (tmp_path / "data" / "users.json").stat().st_mode & 0o777 == 0o600
