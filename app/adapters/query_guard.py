"""查询侧安全前置（在线侧输入处理.md 4.6 横切；13.12.3）。

复用入库侧 InjectionScanner 与 KeywordModerator，在 /api/query 入口执行：
- mode=audit：命中返回 verdict="flag"（调用方记审计 + debug 标记），不拦截；
- mode=reject：命中返回 verdict="blocked"（调用方抛 40002/40003）。

降级原则：检测组件内部异常 → verdict="ok" 放行（安全检测故障不误伤可用性，仅记日志）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.adapters.content_moderation.keyword_moderator import KeywordModerator
from app.adapters.injection_scanner import InjectionScanner
from app.core.logging import get_logger

logger = get_logger("query_guard")


@dataclass
class QueryGuardResult:
    """查询侧安全检查结果。"""

    verdict: str = "ok"                       # ok（放行）| flag（audit 命中）| blocked（reject 命中）
    hits: list[dict] = field(default_factory=list)
    blocked_by: str | None = None             # injection | moderation


class QueryGuard:
    def __init__(self, injection: bool = True, moderation: bool = True,
                 mode: str = "audit",
                 patterns: list[str] | None = None,
                 whitelist: list[str] | None = None,
                 keywords_csv: str = "./configs/moderation_keywords.csv", **_: Any):
        self._injection = injection
        self._moderation = moderation
        self._mode = mode
        self._scanner = InjectionScanner(patterns=patterns or [], whitelist=whitelist or [])
        self._moderator = KeywordModerator(keywords_csv=keywords_csv)

    def check(self, query: str) -> QueryGuardResult:
        if not query or not query.strip():
            return QueryGuardResult()
        try:
            hits: list[dict] = []
            blocked_by: str | None = None
            if self._injection:
                for hit in self._scanner.scan(query):
                    hits.append({"kind": "injection", **hit})
                    blocked_by = "injection"
            if self._moderation:
                mr = self._moderator.moderate(query)
                if mr.verdict == "reject" and mr.matched_rules:
                    hits.append({"kind": "moderation", "rules": mr.matched_rules,
                                 "snippet": mr.snippet[:120]})
                    blocked_by = "moderation"
            if not hits:
                return QueryGuardResult()
            if self._mode == "reject":
                return QueryGuardResult(verdict="blocked", hits=hits, blocked_by=blocked_by)
            return QueryGuardResult(verdict="flag", hits=hits)
        except Exception as exc:  # noqa: BLE001
            logger.warning("查询侧安全检测异常，放行降级：%s", exc)
            return QueryGuardResult()
