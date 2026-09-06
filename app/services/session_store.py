"""会话存储（在线侧输入处理.md L2 4.3.3；13.12.6）。

- 进程内 dict + 锁；TTL 过期自动清除；超 max_sessions 上限 LRU 淘汰最久未用。
- 生产可换 Redis（保持 get/new/put 接口语义即可）；本实现即设计文档的
  「Redis 不可用 → 本地内存缓存（仅单机有效，限制条数）」降级形态。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class Session:
    """单会话状态。history 为 [{role: user|assistant, content}, ...] 轮序列表。"""

    session_id: str
    history: list[dict] = field(default_factory=list)
    last_query: str | None = None
    clarify_count: int = 0            # 连续反问次数（clarification.max_rounds 上限）
    updated_at: float = field(default_factory=time.time)


class SessionStore:
    def __init__(self, ttl_seconds: int = 1800, max_sessions: int = 100):
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session | None:
        """读取会话；不存在或 TTL 过期返回 None（过期条目顺带清理）。"""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self.ttl_seconds > 0 and time.time() - session.updated_at > self.ttl_seconds:
                del self._sessions[session_id]
                return None
            # 命中即移到末尾（LRU 次序由 dict 插入序维护）
            self._sessions.pop(session_id)
            self._sessions[session_id] = session
            return session

    @staticmethod
    def new(session_id: str) -> Session:
        """创建空会话（不落库，由调用方 put 提交）。"""
        return Session(session_id=session_id)

    def put(self, session: Session) -> None:
        """写入/刷新会话；超上限淘汰最久未用。"""
        with self._lock:
            session.updated_at = time.time()
            self._sessions.pop(session.session_id, None)
            self._sessions[session.session_id] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.pop(next(iter(self._sessions)))

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)
