"""内容审核层接口（P-C，入库安全）。

Moderator.moderate(text) -> ModerationResult：
{verdict: "ok"|"reject"|"flag", matched_rules: [{category, keyword}], snippet}
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ModerationResult:
    verdict: str                                    # ok | reject | flag
    matched_rules: list[dict] = field(default_factory=list)   # [{category, keyword}]
    snippet: str = ""


class Moderator(ABC):
    @abstractmethod
    def moderate(self, text: str) -> ModerationResult: ...
