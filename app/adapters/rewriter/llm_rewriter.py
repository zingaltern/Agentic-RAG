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

# 08-Q1：多策略合并调用提示词（单次 LLM 调用同时产出三类变体，省多次往返）
MULTI_PROMPT = (
    "你是企业知识库检索查询改写助手。基于用户查询与检索不足原因，产出三类检索变体：\n"
    "1. rewritten：1~3 个改写/扩句变体（消除口语、补全省略、代词消解）；\n"
    "2. step_back：把具体问题抽象化为一般性的政策/原理问题（如\"张三2024年请了几天年假\"→"
    "\"公司年假天数政策有哪些规定\"）；\n"
    "3. hyde：写下一段 100~200 字的假设性答案段落（想象知识库中理想文档会如何回答，"
    "用于向量检索的文档邻域匹配，不要编造具体数字）。\n"
    "只输出 JSON 对象：{{\"rewritten\": [\"..\"], \"step_back\": \"..\", \"hyde\": \"..\"}}，"
    "未启用的字段输出空字符串，不要输出其他内容。\n"
    "用户查询：{query}\n评估反馈（检索依据不足原因）：{reason}"
)


def _parse_multi(text: str) -> dict:
    """从 LLM 输出解析 JSON 对象（多策略）；失败返回空 dict（触发回退）。"""
    for block in re.findall(r"\{.*\}", text, flags=re.S):
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


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
                 llm_timeout_seconds: int = 30, step_back_enabled: bool = False,
                 hyde_enabled: bool = False, **_: Any):
        self.llm = llm
        self.llm_timeout_seconds = llm_timeout_seconds
        self.step_back_enabled = step_back_enabled   # 08-Q1：Step-Back 抽象化变体（默认关）
        self.hyde_enabled = hyde_enabled             # 08-Q1：HyDE 假设文档变体（默认关）
        self._rule_fallback = RuleRewriter(synonyms_csv=synonyms_csv)

    def rewrite(self, query: str, feedback: EvaluationResult) -> str | list[str] | list[dict]:
        if self.llm is None:
            return self._rule_fallback.rewrite(query, feedback)
        if self.step_back_enabled or self.hyde_enabled:
            return self._rewrite_multi(query, feedback)
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

    def _rewrite_multi(self, query: str, feedback: EvaluationResult) -> list[str | dict]:
        """08-Q1：合并单次 LLM 调用产出 rewritten + step_back + hyde 多策略变体。

        - hyde 变体 path=vector：假设性答案段落靠向量邻域检索生效，
          BM25 对长文本的词面匹配会污染 RRF 排序；
        - 去重（含与原 query 相同者）；任一失败/解析失败/无有效变体 → rule 回退（既有语义）。
        """
        try:
            text = self.llm.chat(
                [{"role": "system", "content": "你是检索查询改写助手，只输出 JSON 对象。"},
                 {"role": "user", "content": MULTI_PROMPT.format(query=query, reason=feedback.reason)}],
                temperature=0.3, max_tokens=512,
            )
            parsed = _parse_multi(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("多策略改写失败，回退 rule：%s", exc)
            return self._rule_fallback.rewrite(query, feedback)

        variants: list[str | dict] = []
        seen = {query}
        for v in (parsed.get("rewritten") or [])[:3]:
            if isinstance(v, str) and v.strip() and v.strip() not in seen:
                variants.append(v.strip())
                seen.add(v.strip())
        step_back = (parsed.get("step_back") or "").strip()
        if self.step_back_enabled and step_back and step_back not in seen:
            variants.append(step_back)
            seen.add(step_back)
        hyde = (parsed.get("hyde") or "").strip()
        if self.hyde_enabled and hyde and hyde not in seen:
            variants.append({"text": hyde, "path": "vector"})
        if not variants:
            logger.info("多策略改写无有效变体，回退 rule")
            return self._rule_fallback.rewrite(query, feedback)
        return variants
