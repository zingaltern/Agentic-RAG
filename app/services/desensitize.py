"""敏感数据脱敏（DEVELOPMENT.md 13.11.1 S5）。

上传通道预留脱敏钩子：身份证（15/18 位）、手机号、银行卡号正则替换为 `***`。
- 纯函数、零依赖；模式集中定义，可测试。
- 默认关闭（ingestion.desensitize_enabled=false）；开启后在 parse 之后、分块之前作用于全文。
"""
from __future__ import annotations

import re

# 身份证：15 位（纯数字）或 18 位（末位可 X）
_ID_CARD = re.compile(r"(?<!\d)(?:\d{15}|\d{17}[\dXx])(?!\d)")
# 中国大陆手机号：1[3-9] 开头 11 位
_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 银行卡号：13~19 位连续数字（避免误伤身份证，单独规则）
_BANK_CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

_PATTERNS = (_ID_CARD, _MOBILE, _BANK_CARD)

MASK = "***"


def desensitize_text(text: str) -> str:
    """把文本中的身份证/手机号/银行卡号替换为 ***；其余原样返回。"""
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(MASK, out)
    return out
