"""入库侧提示注入扫描器（P-C，入库安全）。

- scan(text) 按行/段落扫描"指令型注入模式"（忽略/删除/泄露/绕过系统指令类，中英文），
  返回命中列表 [{pattern, snippet, position}]。
- 内置模式表为模块常量；patterns/whitelist 可经 InjectionConfig 追加/豁免。
- 只匹配整行/整段级别；whitelist 命中子串时该行豁免（防误杀叙述性文本）。
"""
from __future__ import annotations

import re
from typing import Any

# 内置注入模式表（大小写不敏感；含捕获组用于豁免判定）
DEFAULT_PATTERNS: tuple[str, ...] = (
    r"忽略以上|忽略下列|忽略之前的(?:内容|指令|要求)",
    r"ignore (?:all |the )?(?:above|previous|instructions?|system prompt)",
    r"无视系统提示|无视上面的|不要遵守(?:系统)?提示",
    r"删除(?:所有)?数据|清空(?:数据库|知识库)|drop\s+table|delete\s+(?:all\s+)?data",
    r"你(?:现在)?是(?:一个)?(?:免费|无需受约束|不受约束|没有限制)的?助手|bypass (?:the )?system prompt",
    r"reveal (?:your )?(?:system )?prompt|泄露(?:你的)?提示词|打印(?:你的)?系统提示",
)

# 误杀白名单：命中这些子串的行视为普通叙述（如"请勿忽略上级指示"）
DEFAULT_WHITELIST: tuple[str, ...] = (
    "请勿忽略", "不要忽略", "不能忽略", "不可忽略", "切勿忽略",
    "请遵守", "应遵守", "须遵守", "注意", "提醒",
    "do not ignore", "must not ignore", "should not ignore", "please note",
)


class InjectionScanner:
    def __init__(self, patterns: list[str] | None = None,
                 whitelist: list[str] | None = None, **_: Any):
        # patterns 为追加模式：内置模式表始终生效，自定义正则追加其后（whitelist 用于豁免行）
        self._patterns = DEFAULT_PATTERNS + tuple(patterns or ())
        self._whitelist = tuple(whitelist or DEFAULT_WHITELIST)

    def scan(self, text: str) -> list[dict]:
        """按行扫描，返回命中列表 [{pattern, snippet, position}]（position 为行号，从 0 起）。"""
        if not text:
            return []
        hits: list[dict] = []
        for line_no, line in enumerate(text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if any(w in stripped for w in self._whitelist):
                continue
            for pattern in self._patterns:
                if re.search(pattern, stripped, flags=re.IGNORECASE):
                    hits.append({"pattern": pattern, "snippet": stripped[:120],
                                 "position": line_no})
                    break          # 一行命中一个即可，防重复条目
        return hits
