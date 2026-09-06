"""敏感数据脱敏（DEVELOPMENT.md 13.11.1 S5；P2-6 两级化重构）。

- 检测打标：`detect_pii(text)` 返回 [{type, count, sample_hash}]，**不含原文**
  （防敏感信息进 metadata/日志；sample_hash=首个命中 sha256 前 8 位，供跨版本比对）。
- 按策略脱敏：`desensitize_text(text, mode="mask", types=None)`——
  mask=`***`（现行为）/ replace=`[身份证]` 等占位符 / delete=移除；types 过滤类型子集。
- 类型识别按 id_card → phone → bank_card 顺序消费（18 位身份证不再被 bank_card 重复计数）。
- 纯函数、零依赖；默认关闭（ingestion.pii.enabled=false）；在 parse 之后、清洗之前作用于全文。
- 兼容：`desensitize_text(text)` 单参调用与旧行为完全一致（mask + 全类型）。
"""
from __future__ import annotations

import hashlib
import re

# 身份证：15 位（纯数字）或 18 位（末位可 X）
_ID_CARD = re.compile(r"(?<!\d)(?:\d{15}|\d{17}[\dXx])(?!\d)")
# 中国大陆手机号：1[3-9] 开头 11 位
_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 银行卡号：13~19 位连续数字（避免误伤身份证，单独规则）
_BANK_CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")

# 类型注册表（键即对外 type 名；顺序即消费顺序——身份证先消费防止 bank_card 重复计数）
_TYPES: dict[str, re.Pattern] = {
    "id_card": _ID_CARD,
    "phone": _MOBILE,
    "bank_card": _BANK_CARD,
}
_PLACEHOLDERS = {"id_card": "[身份证]", "phone": "[手机号]", "bank_card": "[银行卡]"}

MASK = "***"
VALID_MODES = ("mask", "replace", "delete")
VALID_TYPES = tuple(_TYPES)


def detect_pii(text: str) -> list[dict]:
    """检测 PII（不含原文）：[{type, count, sample_hash}]；按消费顺序去重计数。"""
    if not text:
        return []
    out: list[dict] = []
    working = text
    for ptype, pattern in _TYPES.items():
        matches = pattern.findall(working)
        if not matches:
            continue
        out.append({"type": ptype, "count": len(matches),
                    "sample_hash": hashlib.sha256(matches[0].encode("utf-8")).hexdigest()[:8]})
        working = pattern.sub("\x00", working)   # 已识别命中从后续类型检测中屏蔽
    return out


def desensitize_text(text: str, mode: str = "mask", types: list[str] | None = None) -> str:
    """按策略脱敏：mask=`***` / replace=类型占位符 / delete=移除；types=None 处理全部类型。"""
    if not text:
        return text
    selected = [t for t in (types or list(_TYPES)) if t in _TYPES]
    out = text
    for ptype in selected:
        pattern = _TYPES[ptype]
        if mode == "replace":
            out = pattern.sub(_PLACEHOLDERS[ptype], out)
        elif mode == "delete":
            out = pattern.sub("", out)
        else:
            out = pattern.sub(MASK, out)
    return out
