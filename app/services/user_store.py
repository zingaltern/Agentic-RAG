"""用户存储（V5.1 团队模式）：持久化 data/users.json，scrypt 密码哈希（纯标准库）。

- 密码：hashlib.scrypt（n=2**14, r=8, p=1, dklen=64），salt 每用户随机 16B。
- 文件损坏时回退空表并告警（参照 kb_store._load 容错风格）。
- delete 禁止删自己由路由层校验（store 无"自己"概念）；role 只能 admin 设置。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("user_store")

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 64


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN)
    return salt.hex(), digest.hex()


def verify_password(password: str, salt_hex: str, expected_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN)
    return secrets.compare_digest(digest, expected)


@dataclass
class User:
    user_id: str
    username: str
    name: str
    role: str                          # admin | user
    password_salt: str = ""
    password_hash: str = ""
    created_at: float = 0.0
    last_login: float | None = None


class UserStore:
    def __init__(self, persist_file: str = "data/users.json"):
        self._persist_file = persist_file
        self._lock = threading.Lock()
        self._users: dict[str, User] = {}
        self._by_username: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        path = Path(self._persist_file)
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw:
                user = User(
                    user_id=item["user_id"], username=item["username"],
                    name=item.get("name", item["username"]), role=item.get("role", "user"),
                    password_salt=item.get("password_salt", ""),
                    password_hash=item.get("password_hash", ""),
                    created_at=item.get("created_at", 0.0),
                    last_login=item.get("last_login"),
                )
                self._users[user.user_id] = user
                self._by_username[user.username] = user.user_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("users.json 读取失败（%s）：%s，回退空表", path, exc)

    def _persist(self) -> None:
        try:
            Path(self._persist_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self._persist_file).write_text(
                json.dumps([self._to_dict(u) for u in self._users.values()],
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("users.json 写入失败：%s", exc)

    @staticmethod
    def _to_dict(user: User) -> dict:
        return {"user_id": user.user_id, "username": user.username, "name": user.name,
                "role": user.role, "password_salt": user.password_salt,
                "password_hash": user.password_hash, "created_at": user.created_at,
                "last_login": user.last_login}

    def create(self, username: str, password: str, name: str = "", role: str = "user") -> User:
        username = username.strip()
        if not username:
            raise ValueError("username 不能为空")
        if role not in ("admin", "user"):
            raise ValueError(f"非法角色: {role!r}")
        salt_hex, hash_hex = hash_password(password)
        with self._lock:
            if username in self._by_username:
                raise ValueError(f"用户名已存在: {username}")
            user = User(user_id=secrets.token_hex(6), username=username,
                        name=name.strip() or username, role=role,
                        password_salt=salt_hex, password_hash=hash_hex,
                        created_at=time.time())
            self._users[user.user_id] = user
            self._by_username[username] = user.user_id
        self._persist()
        return user

    def authenticate(self, username: str, password: str) -> User | None:
        with self._lock:
            user = self._users.get(self._by_username.get(username.strip(), ""))
        if user is None or not verify_password(password, user.password_salt, user.password_hash):
            return None
        user.last_login = time.time()
        self._persist()
        return user

    def get_by_id(self, user_id: str) -> User | None:
        with self._lock:
            return self._users.get(user_id)

    def get_by_username(self, username: str) -> User | None:
        with self._lock:
            return self._users.get(self._by_username.get(username.strip(), ""))

    def list(self) -> list[User]:
        with self._lock:
            return sorted(self._users.values(), key=lambda u: u.created_at)

    def update_role(self, user_id: str, role: str) -> User | None:
        if role not in ("admin", "user"):
            raise ValueError(f"非法角色: {role!r}")
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                return None
            user.role = role
        self._persist()
        return user

    def reset_password(self, user_id: str, new_password: str) -> bool:
        with self._lock:
            user = self._users.get(user_id)
            if user is None:
                return False
            user.password_salt, user.password_hash = hash_password(new_password)
        self._persist()
        return True

    def delete(self, user_id: str) -> bool:
        with self._lock:
            user = self._users.pop(user_id, None)
            if user is None:
                return False
            self._by_username.pop(user.username, None)
        self._persist()
        return True

    def is_empty(self) -> bool:
        with self._lock:
            return len(self._users) == 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._users)
