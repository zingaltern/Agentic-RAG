"""Agent 结构化输出解析（P-D）。"""
from __future__ import annotations

import json
from typing import Any

from app.core.errors import AgentProtocolError


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 输出中提取第一个合法 JSON 对象。

    支持：纯 JSON、```json 围栏、前后有解释文字。按括号配对扫描，避免正则贪婪误吞。
    """
    if not text or not text.strip():
        raise AgentProtocolError("LLM 返回空内容")
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    start = raw.find("{")
    if start < 0:
        raise AgentProtocolError("LLM 输出中没有 JSON 对象")
    depth = 0
    in_str = False
    escape = False
    for idx in range(start, len(raw)):
        ch = raw[idx]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                block = raw[start:idx + 1]
                try:
                    parsed = json.loads(block)
                except json.JSONDecodeError as exc:
                    raise AgentProtocolError(f"JSON 解析失败: {exc}") from exc
                if not isinstance(parsed, dict):
                    raise AgentProtocolError("JSON 顶层必须是对象")
                return parsed
    raise AgentProtocolError("JSON 对象未闭合")
