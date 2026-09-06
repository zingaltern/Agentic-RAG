"""知识库元数据存储（DEVELOPMENT.md 13.5）：kb_id → collection 映射。

- 内存 dict + 持久化 data/kb.json（损坏时回退内置 default，不崩溃）。
- 内置默认库 `default`（collection 沿用 "kb"，存量零迁移），不可删除。
- kb_id 规则：name slug 化（小写+下划线，^[a-z0-9_-]{1,32}$），非法/冲突追加 4 位短 hash。
- 事件钩子 on_kb_changed（增删时触发，供后续增量索引/多端同步接入，P3 预留）。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from app.core.logging import get_logger
from app.core.persist import atomic_write_json

logger = get_logger("kb_store")

KB_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
DEFAULT_KB_ID = "default"
DEFAULT_COLLECTION = "kb"          # default 库沿用存量 collection（13.5 决策，零迁移）


@dataclass
class KbInfo:
    kb_id: str
    name: str
    description: str = ""
    collection: str = DEFAULT_COLLECTION
    owner: str = "admin"             # V5.1：私有库归属（shared 库 owner 无实际意义）
    scope: str = "shared"            # V5.1：shared|private
    created_at: float = 0.0


def slugify(name: str) -> str:
    """name → kb_id 基底：小写，非 [a-z0-9] 转下划线，去重/去头尾下划线。"""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug


class KbStore:
    def __init__(self, persist_file: str = "data/kb.json", default_kb: str = DEFAULT_KB_ID):
        self._persist_file = persist_file
        self.default_kb = default_kb
        self._lock = threading.Lock()
        self._kbs: dict[str, KbInfo] = {
            default_kb: KbInfo(kb_id=default_kb, name="默认知识库",
                               description="内置默认知识库（不可删除）",
                               collection=DEFAULT_COLLECTION,
                               owner="admin", scope="shared", created_at=time.time()),
        }
        self.on_kb_changed: list[Callable[[str, str], None]] = []   # (event: create|delete, kb_id)
        self._load()

    # -- 持久化 ----------------------------------------------------------------

    def _load(self) -> None:
        path = Path(self._persist_file)
        if not path.exists():
            return
        migrated = False
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw:
                kb_id = item.get("kb_id")
                if not kb_id or kb_id == self.default_kb:
                    continue
                # V5.1 kb.json v2 兼容迁移：旧记录（无 owner/scope）补默认值
                if "owner" not in item or "scope" not in item:
                    migrated = True
                self._kbs[kb_id] = KbInfo(
                    kb_id=kb_id,
                    name=item.get("name", kb_id),
                    description=item.get("description", ""),
                    collection=item.get("collection", kb_id),
                    owner=item.get("owner", "admin"),
                    scope=item.get("scope", "shared"),
                    created_at=item.get("created_at", 0.0),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb.json 读取失败（%s）：%s，回退内置 default", path, exc)
        if migrated:
            self._persist()

    def _persist(self) -> None:
        try:
            atomic_write_json(self._persist_file, [self._to_dict(k) for k in self._kbs.values()])  # S7
        except Exception as exc:  # noqa: BLE001
            logger.warning("kb.json 写入失败：%s", exc)

    @staticmethod
    def _to_dict(info: KbInfo) -> dict:
        return {"kb_id": info.kb_id, "name": info.name, "description": info.description,
                "collection": info.collection, "owner": info.owner, "scope": info.scope,
                "created_at": info.created_at}

    # -- 事件钩子（P3 预留：增量索引/多端同步） ----------------------------------

    def _notify(self, event: str, kb_id: str) -> None:
        for hook in self.on_kb_changed:
            try:
                hook(event, kb_id)
            except Exception:  # noqa: BLE001
                logger.warning("kb 变更钩子执行失败（%s %s）", event, kb_id)

    # -- CRUD -------------------------------------------------------------------

    def create(self, name: str, description: str = "",
               owner: str = "admin", scope: str = "shared") -> KbInfo:
        """按 name 生成 kb_id（slug 化 + 冲突/非法追加 4 位短 hash）。"""
        if not name or not name.strip():
            raise ValueError("name 不能为空")
        if scope not in ("shared", "private"):
            raise ValueError(f"非法 scope: {scope!r}")
        base = slugify(name) or "kb"
        kb_id = base
        if not KB_ID_RE.match(kb_id):
            kb_id = "kb"
        with self._lock:
            # C1（V5.1）：collection 必须唯一（与默认库 "kb" 及其它库冲突即追加 hash），
            # 否则两个库会共享同一 Chroma 集合，破坏 13.5「一库一 collection」数据隔离
            used_collections = {info.collection for info in self._kbs.values()}
            if kb_id in self._kbs or kb_id in used_collections:
                kb_id = f"{base}_{hashlib.md5(name.encode('utf-8')).hexdigest()[:4]}"
                if kb_id in self._kbs or kb_id in used_collections:      # 极端冲突：再追加
                    kb_id = f"kb_{hashlib.md5(name.encode('utf-8')).hexdigest()[:8]}"
            info = KbInfo(kb_id=kb_id, name=name.strip(), description=description,
                          collection=kb_id, owner=owner, scope=scope, created_at=time.time())
            self._kbs[kb_id] = info
        self._persist()
        self._notify("create", kb_id)
        return info

    def get(self, kb_id: str) -> KbInfo | None:
        with self._lock:
            return self._kbs.get(kb_id)

    def update(self, kb_id: str, **fields) -> KbInfo:
        """更新已存在库的可变字段（P1-3：collection 指针切换；P2-6 将扩展 pii 库级策略）。

        - 仅允许白名单字段（collection/owner/scope/description），非法字段 ValueError；
        - collection 切换为元数据级原子（单次 atomic_write_json），失败原样上抛由调用方处理；
        - 触发 "update" 事件钩子。
        """
        allowed = {"collection", "owner", "scope", "description"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"不允许更新的字段: {sorted(unknown)}")
        with self._lock:
            info = self._kbs.get(kb_id)
            if info is None:
                raise KeyError(f"知识库不存在: {kb_id!r}")
            if "collection" in fields:
                if not isinstance(fields["collection"], str) or not fields["collection"]:
                    raise ValueError("collection 必须为非空字符串")
                used = {i.collection for kid, i in self._kbs.items() if kid != kb_id}
                if fields["collection"] in used:
                    raise ValueError(f"collection 已被其它库占用: {fields['collection']!r}")
            if "scope" in fields and fields["scope"] not in ("shared", "private"):
                raise ValueError(f"非法 scope: {fields['scope']!r}")
            for key, value in fields.items():
                setattr(info, key, value)
        self._persist()
        self._notify("update", kb_id)
        return info

    def exists(self, kb_id: str) -> bool:
        with self._lock:
            return kb_id in self._kbs

    def list(self) -> list[KbInfo]:
        with self._lock:
            return sorted(self._kbs.values(), key=lambda k: (k.kb_id != self.default_kb, k.kb_id))

    def delete(self, kb_id: str) -> bool:
        """删除映射；default 拒绝（返回 False）。"""
        if kb_id == self.default_kb:
            return False
        with self._lock:
            removed = self._kbs.pop(kb_id, None)
        if removed is None:
            return False
        self._persist()
        self._notify("delete", kb_id)
        return True

    def collection_of(self, kb_id: str) -> str | None:
        """kb_id → chroma collection 名（default → "kb"）。"""
        info = self.get(kb_id)
        return info.collection if info else None

    def __len__(self) -> int:
        with self._lock:
            return len(self._kbs)
