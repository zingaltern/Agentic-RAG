"""规则查询标准化器（在线侧输入处理.md L1；13.12.2）。

按序执行（每步独立 enabled 子开关）：
1. filter_invalid_chars：Emoji/控制字符清理 + 连续重复标点压缩（！！！→！）；
2. asr_smooth：语音转写语气词清理（filler 词典可配，最长优先）+ 连续单字重复压缩（对对对→对）；
3. resolve_relative_time：相对时间 → 绝对时间（昨天→2026-08-17；reference 缺省取当天）。

降级原则：每步独立 try/except，异常即跳过该步保留上一步文本；
整体输出必为合法文本，最差等于输入（绝不抛错、绝不返回空）。
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from app.core.interfaces import QueryNormalizer
from app.core.logging import get_logger

logger = get_logger("normalizer")

# Emoji/符号区（含杂项符号、箭头、变体选择符、ZWJ 序列残留）
_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u2B00-\u2BFF\u2190-\u21FF\u200D]+"
)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 连续重复标点压缩（保留单个；"！！！"→"！"，"，，，"→"，"）
_PUNCT_COMPRESS_RE = re.compile(r"([!！?？。，、；：~～])\1{2,}")
# 连续单字重复压缩（"对对对"→"对"；疑问词连续如"？？"已在标点步处理）
_CHAR_REPEAT_RE = re.compile(r"(.)\1{2,}")

# 相对时间词 → 相对参考日偏移（最长词优先匹配）
_RELATIVE_DAYS: dict[str, int] = {
    "大前天": -3, "前天": -2, "昨天": -1, "今日": 0, "今天": 0,
    "明天": 1, "后天": 2, "大后天": 3,
}
# 年份级 → 绝对年份
_RELATIVE_YEARS: dict[str, int] = {
    "前年": -2, "去年": -1, "今年": 0, "明年": 1,
}


class RuleQueryNormalizer(QueryNormalizer):
    def __init__(self, filter_invalid_chars: bool = True, asr_smooth: bool = True,
                 asr_filler_words: list[str] | None = None,
                 resolve_relative_time: bool = True,
                 time_reference_date: str | None = None,
                 timezone: str = "Asia/Shanghai", **_: Any):
        self.filter_invalid_chars = filter_invalid_chars
        self.asr_smooth = asr_smooth
        self.resolve_relative_time = resolve_relative_time
        self.filler_words = [w for w in (asr_filler_words or []) if w]
        self.timezone = timezone
        self._ref_date: date | None = None
        if time_reference_date:
            try:
                self._ref_date = date.fromisoformat(time_reference_date)
            except ValueError:
                logger.warning("time_reference_date 非法（%s），回退当天", time_reference_date)

    def normalize(self, query: str) -> str:
        text = query
        if self.filter_invalid_chars:
            text = self._safe(text, self._filter_invalid)
        if self.asr_smooth:
            text = self._safe(text, self._asr_smooth)
        if self.resolve_relative_time:
            text = self._safe(text, self._resolve_time)
        return text or query

    # -- 降级包装：单步异常 → 保留上一步文本 ------------------------------------

    @staticmethod
    def _safe(text: str, fn) -> str:
        try:
            out = fn(text)
            return out if out else text
        except Exception as exc:  # noqa: BLE001
            logger.warning("标准化步骤异常，跳过该步（降级保留原文本）：%s", exc)
            return text

    # -- 步骤 ------------------------------------------------------------------

    @staticmethod
    def _filter_invalid(text: str) -> str:
        out = _EMOJI_RE.sub("", text)
        out = _CONTROL_RE.sub("", out)
        out = _PUNCT_COMPRESS_RE.sub(lambda m: m.group(1), out)
        return out

    def _asr_smooth(self, text: str) -> str:
        # 1) 语气填充词（最长优先，避免"那个"先拆掉"这个那个"等复合词）
        for word in sorted(self.filler_words, key=len, reverse=True):
            if word:
                text = text.replace(word, "")
        # 2) 连续单字重复压缩
        text = _CHAR_REPEAT_RE.sub(r"\1", text)
        return re.sub(r"\s+", " ", text).strip()

    def _resolve_time(self, text: str) -> str:
        ref = self._ref_date or date.today()
        for word, offset in _RELATIVE_DAYS.items():
            if word in text:
                target = ref + timedelta(days=offset)
                text = text.replace(word, target.isoformat())
        for word, offset in _RELATIVE_YEARS.items():
            if word in text:
                text = text.replace(word, str(ref.year + offset))
        return text
