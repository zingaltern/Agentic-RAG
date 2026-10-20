"""解析器注册表与路由链（DEVELOPMENT.md 5.1 P3-8）。

- `ParserSpec(name, extensions, fn, priority)`：`fn(raw, config=None) -> (text, metadata)`；
- `PARSER_REGISTRY`：按 name 注册（链式路由按名引用）；
- `PARSERS`：按扩展名展开（兼容旧单实现分发路径，扩展名为空不展开，如 ocr_dummy）；
- `_routing_chain`：从 `ingestion.parser_routing` 取该扩展名的降级链（空 = 旧路径）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ParserSpec:
    """解析器注册规格（P3-8）：name 为路由/溯源标识；extensions 带点；priority 预留排序。"""

    name: str
    extensions: tuple[str, ...]
    fn: Callable
    priority: int = 100


# 按 name 注册（降级链按名引用）
PARSER_REGISTRY: dict[str, ParserSpec] = {}
# 按扩展名展开（旧单实现分发路径；扩展名为空的实现不入此表）
PARSERS: dict[str, Callable] = {}


def _register_parser(spec: ParserSpec) -> ParserSpec:
    """注册实现，并同步展开到 PARSERS（扩展名为空则不展开，如 ocr_dummy）。"""
    PARSER_REGISTRY[spec.name] = spec
    for ext in spec.extensions:
        PARSERS[ext] = spec.fn
    return spec


def _routing_chain(ext: str, routing: dict) -> list[str]:
    """P3-8：取扩展名的解析链（key 兼容带点/不带点；非法形状返回空链=旧路径）。"""
    entry = routing.get(ext.lstrip(".")) or routing.get(ext) or {}
    chain = entry.get("chain") if isinstance(entry, dict) else None
    return [str(n) for n in chain] if isinstance(chain, list) else []
