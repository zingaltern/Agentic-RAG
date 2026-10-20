"""JSON / JSONL 解析（DEVELOPMENT.md 16.3）：路径扁平化，不保留原文。

- dict → `a.b`，list → `a[0]`，标量 → `路径: 值`；
- JSONL 每条记录前缀 `[N].`；上限由 `ingestion.json_flatten` 控制。
"""
from __future__ import annotations

import json

from app.adapters.parser.common import _decode_text
from app.core.errors import ParseError


def _json_limits(config: object | None) -> tuple[int, int]:
    """取 JSON 扁平化上限（`ingestion.json_flatten`；缺配置用默认值）。"""
    cfg = getattr(config, "json_flatten", None)
    max_items = int(getattr(cfg, "max_items", 5000)) if cfg is not None else 5000
    max_depth = int(getattr(cfg, "max_depth", 12)) if cfg is not None else 12
    return max(1, max_items), max(1, max_depth)

def _scalar_text(value: object) -> str:
    """JSON 标量 → 文本（与 JSON 字面量口径一致：null/true/false/数字/字符串）。"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)

def _flatten_json(value: object, prefix: str, depth: int, out: list[str],
                  max_items: int, max_depth: int) -> None:
    """路径扁平化（16.3）：dict → `a.b`，list → `a[0]`，标量 → `路径: 值`。

    达到 max_items 立即返回（由调用方补截断标记）；深度超过 max_depth 写 `<max-depth>` 占位。
    """
    if len(out) >= max_items:
        return
    if depth > max_depth:
        out.append(f"{prefix}: <max-depth>" if prefix else "<max-depth>")
        return
    if isinstance(value, dict):
        if not value:
            out.append(f"{prefix}: {{}}" if prefix else "{}")
            return
        for key, sub in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            _flatten_json(sub, child, depth + 1, out, max_items, max_depth)
    elif isinstance(value, list):
        if not value:
            out.append(f"{prefix}: []" if prefix else "[]")
            return
        for index, sub in enumerate(value):
            _flatten_json(sub, f"{prefix}[{index}]", depth + 1, out, max_items, max_depth)
    else:
        out.append(f"{prefix}: {_scalar_text(value)}" if prefix else _scalar_text(value))

def _flatten_json_document(data: object, config: object | None) -> str:
    """解析后的 JSON 结构 → 扁平文本（含 max_items 截断标记）。"""
    max_items, max_depth = _json_limits(config)
    lines: list[str] = []
    _flatten_json(data, "", 0, lines, max_items, max_depth)
    if len(lines) >= max_items:
        lines.append(f"…（已截断，共 {max_items} 行）")
    return "\n".join(lines)

def _parse_json(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """JSON（16.3）：整体解析后路径扁平化；空文件 → 空文本；非法 JSON → ParseError 30003。"""
    text = _decode_text(raw)
    if not text.strip():
        return "", {"page": None, "author": None}      # 空文件沿用既有语义（同 txt）
    try:
        data = json.loads(text)
    except Exception as exc:
        raise ParseError(f"JSON 解析失败（格式非法）: {exc}") from exc
    return _flatten_json_document(data, config), {"page": None, "author": None}

def _parse_jsonl(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """JSONL（16.3）：每行一个 JSON 值，路径前缀 `[N].`（N 从 1 起）。"""
    text = _decode_text(raw)
    max_items, max_depth = _json_limits(config)
    lines: list[str] = []
    record_count = 0
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except Exception as exc:
            raise ParseError(f"JSONL 第 {line_no} 行解析失败: {exc}") from exc
        record_count += 1
        if len(lines) >= max_items:
            break
        _flatten_json(data, f"[{record_count}]", 0, lines, max_items, max_depth)
    if len(lines) >= max_items:
        lines.append(f"…（已截断，共 {max_items} 行）")
    return "\n".join(lines), {"page": None, "author": None, "records": record_count}
