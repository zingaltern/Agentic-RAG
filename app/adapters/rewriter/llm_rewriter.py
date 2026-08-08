"""LLM 查询改写器（DEVELOPMENT.md 13.3.2）。

- 输入 (query, reason)，调用 DeepSeek 输出 1~3 个改写/扩句变体（JSON 数组，含代词消解）。
- 超时 30s（llm_timeout_seconds）；失败/超时/限流/解析失败 → 自动回退 rule 改写（单变体）。
- 仅当 result.passed=False 时由编排调用；返回 list[str]（1~3 变体）。
- 兼容 QueryRewriter 契约：返回 str（回退）或 list[str]（LLM 多变体），编排层按类型适配。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.adapters.rewriter.rule_rewriter import RuleRewriter
from app.core.domain import EvaluationResult
from app.core.errors import LLMUnavailableError
from app.core.interfaces import LLM, QueryRewriter
from app.core.logging import get_logger

logger = get_logger("rewriter")

PROMPT = (
    "你是企业知识库检索查询改写助手。把用户的查询改写成 1~3 个更适合检索的变体："
    "消除口语、补全省略、代词消解（如\"我是哪个大学毕业的\"→\"我的毕业院校\"）。"
    "只输出 JSON 数组（如 [\"变体1\", \"变体2\"]），不要输出其他内容。\n"
    "用户查询：{query}\n评估反馈（检索依据不足原因）：{reason}"
)


def _parse_variants(text: str) -> list[str]:
    """从 LLM 输出解析 JSON 数组；失败返回空列表（触发回退）。"""
    candidates: list[str] = []
    for block in re.findall(r"\[[^\]]*\]", text, flags=re.S):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list):
            candidates.extend(p for p in parsed if isinstance(p, str) and p.strip())
    return list(dict.fromkeys(c.strip() for c in candidates))


class LLMRewriter(QueryRewriter):
    def __init__(self, llm: LLM | None = None, synonyms_csv: str = "./configs/synonyms.csv",
                 llm_timeout_seconds: int = 30, **_: Any):
        self.llm = llm
        self.llm_timeout_seconds = llm_timeout_seconds
        self._rule_fallback = RuleRewriter(synonyms_csv=synonyms_csv)

    def rewrite(self, query: str, feedback: EvaluationResult) -> str | list[str]:
        if self.llm is None:
            return self._rule_fallback.rewrite(query, feedback)
        try:
            text = self.llm.chat(
                [{"role": "system", "content": "你是检索查询改写助手，输出 JSON 数组。"},
                 {"role": "user", "content": PROMPT.format(query=query, reason=feedback.reason)}],
                temperature=0.3, max_tokens=256,
            )
            variants = _parse_variants(text)
        except LLMUnavailableError as exc:
            logger.warning("LLM 改写失败，回退 rule：%s", exc)
            return self._rule_fallback.rewrite(query, feedback)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 改写异常，回退 rule：%s", exc)
            return self._rule_fallback.rewrite(query, feedback)

        variants = [v for v in variants if v and v != query]
        if not variants:
            logger.info("LLM 改写无有效变体，回退 rule")
            return self._rule_fallback.rewrite(query, feedback)
        return variants[:3]
