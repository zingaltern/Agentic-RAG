"""规则改写器（DEVELOPMENT.md 5.6）：确定性、无 LLM。

按序执行：
1. 全角转半角、压缩连续空白（QueryNormalize 同款归一）。
2. 口语 → 书面词典替换（如 咋→怎么、行不行→是否可行），最长词优先。
3. 同义词扩充：读 synonyms.csv（每行一组，逗号分隔）；文件缺失/损坏时用内置小词表，不失败。
4. 删停用词（仅语气/口语填充词，疑问词保留）；feedback.reason==empty_context 时
   额外剔除单字符词（重切长词）。
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

from app.core.domain import EvaluationResult
from app.core.interfaces import QueryRewriter
from app.core.logging import get_logger

logger = get_logger("rewriter")

# 口语 → 书面（确定性词典；最长匹配优先）
_SPOKEN_TO_WRITTEN: dict[str, str] = {
    "行不行": "是否可行",
    "咋整": "怎么办",
    "咋样": "怎么样",
    "咋": "怎么",
    "着儿": "",
    "好几个": "几个",
    "好多": "多少",
    "多长时间": "多久",
    "要几天": "需几天",
    "能攒到": "最高累计",
}

# 内置同义词小词表（synonyms.csv 缺失时的兜底，格式与文件一致）
_BUILTIN_SYNONYMS: list[list[str]] = [
    ["年假", "休假", "假期"],
    ["报销", "报账", "发票"],
    ["出差", "公出", "外出"],
    ["病假", "医疗假"],
    ["调休", "补休"],
    ["账号", "账户", "工号"],
    ["密码", "口令"],
    ["提交", "申报", "申请"],
]

# 停用词（改写阶段删除；用多字短语，避免拆碎领域词如"年假"）
# 注意：不删疑问词（怎么/如何/什么/是否/需要等）——删词损失查询意图，且对检索召回无益；
# 仅删语气/口语填充词。
_STOP_PHRASES = (
    "请问一下", "问一下", "请问", "可以", "能够", "这个",
    "那个", "一个", "进行", "以及", "或者", "然后", "就是",
    "知道", "想问", "呢", "吗", "吧", "啊", "呀", "哦", "嘛", "啥",
)

# empty_context 反馈时额外剔除的单字停用词（5.6 第 4 条：先删停用词再重切长词）
_EMPTY_EXTRA_STOPS = ("的", "了", "在", "我", "你", "他", "她", "它", "要", "能", "会", "个", "到")


def normalize_query(query: str) -> str:
    """全角转半角 + 压缩连续空白（内置确定性归一，5.9 QueryNormalize）。"""
    text = query
    for full, half in _FULL_TO_HALF.items():
        text = text.replace(full, half)
    return re.sub(r"\s+", " ", text).strip()


_FULL_TO_HALF = {
    "！": "!", "？": "?", "，": ",", "。": ".", "：": ":", "；": ";", "（": "(", "）": ")",
    "“": '"', "”": '"', "‘": "'", "’": "'", "—": "-", "％": "%", "　": " ",
}


def _load_synonym_groups(path: str) -> list[list[str]]:
    """读 synonyms.csv；缺失/损坏 → 内置词表兜底，绝不抛异常。"""
    p = Path(path)
    if not p.exists():
        logger.warning("synonyms.csv 不存在（%s），使用内置同义词表", path)
        return _BUILTIN_SYNONYMS
    try:
        with open(p, "r", encoding="utf-8") as fh:
            rows = [row for row in csv.reader(fh) if row]
        groups = [[w.strip() for w in row if w.strip()] for row in rows]
        groups = [g for g in groups if len(g) >= 2]
        return groups or _BUILTIN_SYNONYMS
    except Exception as exc:
        logger.warning("synonyms.csv 读取失败（%s）：%s，使用内置同义词表", path, exc)
        return _BUILTIN_SYNONYMS


class RuleRewriter(QueryRewriter):
    def __init__(self, synonyms_csv: str = "./configs/synonyms.csv", **_: Any):
        self.synonym_groups = _load_synonym_groups(synonyms_csv)

    def rewrite(self, query: str, feedback: EvaluationResult) -> str:
        text = normalize_query(query)

        # 1) 删停用词（短语级替换，避免拆碎领域词；最长优先）
        #    先于口语替换：否则"怎么样"里的"怎么"、"是否可行"里的"是否"会被误删
        for sw in sorted(_STOP_PHRASES, key=len, reverse=True):
            if sw:
                text = text.replace(sw, "")

        # 2) empty_context 时额外剔除单字停用词（5.6 第 4 条）
        if feedback.reason == "empty_context":
            for sw in _EMPTY_EXTRA_STOPS:
                text = text.replace(sw, "")

        # 3) 口语 → 书面（最长词优先，避免"咋"先替换破坏"咋整"）
        for spoken, written in sorted(_SPOKEN_TO_WRITTEN.items(), key=lambda kv: len(kv[0]), reverse=True):
            if spoken:
                text = text.replace(spoken, written)

        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return query

        # 4) 同义词扩充：命中任一组内词 → 补上同组其他词
        for group in self.synonym_groups:
            hits = [w for w in group if w in text]
            if hits:
                missing = [w for w in group if w not in text]
                if missing:
                    text = text + " " + " ".join(missing)
        return text
