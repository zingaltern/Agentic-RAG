"""文本清洗小工具（跨编排/Agent 复用）。"""
from __future__ import annotations

import re

_CITATION_RE = re.compile(r"\[\d+\]")


def strip_citations(text: str) -> str:
    """移除 [N] 引用标号；历史回答的编号只对当时上下文有效，注入前必须剥离。"""
    return _CITATION_RE.sub("", text or "")
