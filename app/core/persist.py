"""持久化安全工具（S7，V5.1）：原子写 + 最小权限。

- 同目录临时文件写入 → os.replace 原子替换（崩溃/并发不产生半截 JSON 或旧快照覆盖）。
- 落盘权限 0o600（users.json 含密码哈希，其余 store 同规格）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: str | Path, payload: object, mode: int = 0o600) -> None:
    """原子写 JSON：临时文件 + fsync + os.replace，权限收紧为 mode（默认 0o600）。

    写入失败时清理临时文件并向上抛异常（调用方按既有容错策略降级为内存态）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_bytes(path: str | Path, data: bytes, mode: int = 0o600) -> None:
    """原子写二进制：临时文件 + fsync + os.replace（P0-2 原件归档用；父目录自动创建）。

    与 atomic_write_json 同一套原子语义：崩溃/并发不产生半截文件；
    写入失败清理临时文件并向上抛异常（调用方按归档"尽力而为"策略降级）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
