"""auto 模式规则路由（DEVELOPMENT.md 17.2）：不调用 LLM 的复杂度打分。"""
from __future__ import annotations

from typing import Any

_COMPARE = ("对比", "比较", "区别", "差异", "分别", "各自", "不同")
_AGG = ("汇总", "统计", "一共", "总共", "列出", "哪些", "有什么", "分别有哪些")
_MULTI = ("和", "与", "以及", "还有", "同时")
_PROCEDURE = ("流程", "步骤", "先后", "如何办理", "怎么办理", "所需材料")
_EXPLICIT = ("详细", "全面", "深度", "逐条", "分别说明")


def complexity_score(query: str) -> int:
    """确定性信号打分；阈值见 AgentRoutingConfig（默认 >=3 agentic，<=1 reflect）。"""
    text = (query or "").strip()
    score = 0
    if text.count("？") + text.count("?") >= 2:
        score += 2
    if any(k in text for k in _COMPARE):
        score += 2
    if any(k in text for k in _AGG):
        score += 2
    if any(k in text for k in _MULTI):
        score += 2
    if any(k in text for k in _PROCEDURE):
        score += 1
    if len(text) > 60:
        score += 1
    if any(k in text for k in _EXPLICIT):
        score += 1
    # 书名号/文件名线索：跨文档定位
    if "《" in text or "》" in text or ".md" in text.lower() or ".pdf" in text.lower():
        score += 2
    return score


def route_decision(query: str, config: Any, history: list[dict] | None = None) -> str:
    """返回 direct / agentic / reflect_first（不确定，先 reflect 失败再升级）。"""
    routing = getattr(config, "routing", None)
    if routing is None or not getattr(routing, "enabled", True):
        # auto 关闭时绝不升级 agentic；显式 mode=agentic 不走本函数
        return "direct"
    score = complexity_score(query)
    if history:
        score += 2 if len(history) >= 2 else 0
    if score >= int(getattr(routing, "agentic_threshold", 3)):
        return "agentic"
    if score <= int(getattr(routing, "reflect_threshold", 1)):
        return "direct"
    return "reflect_first"
