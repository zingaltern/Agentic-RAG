"""解析分发入口（DEVELOPMENT.md 5.1）。

`parse_file`：按扩展名分发 → 预检（魔数/zip）→ 单实现路径或 P3-8 降级链 → 统一产出 Document。
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from app.adapters.parser.common import _build_document
from app.adapters.parser.formats import SUPPORTED_EXTENSIONS
from app.adapters.parser.quality import evaluate_parse_quality
from app.adapters.parser.registry import PARSER_REGISTRY, PARSERS, _routing_chain
from app.adapters.parser.security import _check_magic
from app.core.domain import Document
from app.core.errors import ParseError, QualityGateError
from app.core.logging import get_logger

logger = get_logger("parser")


def parse_file(file_stream: BinaryIO, filename: str, config: object | None = None) -> Document:
    """按扩展名分发解析；统一异常包装为 ParseError（5.1）；S2 魔数/zip 炸弹预检。"""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ParseError(f"不支持的文档格式: {filename!r}（支持：{sorted(e for e in SUPPORTED_EXTENSIONS)}）")
    try:
        raw = file_stream.read()
    except Exception as exc:
        raise ParseError(f"读取文件失败: {filename}") from exc
    if not isinstance(raw, bytes):
        raw = bytes(raw)
    _check_magic(ext, raw, filename, config)

    routing = getattr(config, "parser_routing", None) or {} if config is not None else {}
    chain_names = _routing_chain(ext, routing)
    if not chain_names:
        # 旧路径（默认）：单实现分发，无链式降级、无 parser 溯源元数据（零差异承诺）
        try:
            parser = PARSERS.get(ext)
            if parser is None:
                raise ParseError(f"不支持的文档格式: {filename!r}")
            text, meta = parser(raw, config)
        except ParseError:
            raise
        except Exception as exc:
            raise ParseError(f"{filename} 解析失败: {exc}") from exc
        return _build_document(filename, raw, text or "", meta)

    # P3-8：链式解析（路由非空时启用）——按序尝试，抛错/空文本降级，成功携带溯源元数据
    gate_cfg = getattr(config, "quality_gate", None)
    min_chars = gate_cfg.min_text_chars if gate_cfg is not None else 30
    specs = [PARSER_REGISTRY[n] for n in chain_names if n in PARSER_REGISTRY]
    for name in chain_names:
        if name not in PARSER_REGISTRY:
            logger.warning("解析链含未注册实现（跳过）：%s", name,
                           extra={"extra_fields": {"filename": filename, "parser": name}})
    if not specs:
        raise ParseError(f"{filename} 解析链无可用实现: {chain_names}")
    errors: list[str] = []
    for spec in specs:
        try:
            text, meta = spec.fn(raw, config)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{spec.name}: {exc}")
            logger.warning("解析器降级（抛错，尝试链内下一实现）",
                           extra={"extra_fields": {"filename": filename,
                                                   "parser": spec.name, "error": str(exc)}})
            continue
        flags = evaluate_parse_quality(text or "", (meta or {}).get("text_stats") or {},
                                       min_text_chars=min_chars,
                                       max_garbled_ratio=(gate_cfg.max_garbled_ratio
                                                          if gate_cfg is not None else 0.05),
                                       min_pages_with_text_ratio=(gate_cfg.min_pages_with_text_ratio
                                                                  if gate_cfg is not None else 0.2))
        if flags:
            errors.append(f"{spec.name}: {','.join(flags)}")
            logger.warning("解析器降级（产出质量不达标，尝试链内下一实现）",
                           extra={"extra_fields": {"filename": filename,
                                                   "parser": spec.name, "flags": flags}})
            continue
        meta = dict(meta or {})
        meta["parser"] = spec.name
        meta["parser_version"] = "1"
        return _build_document(filename, raw, text or "", meta)
    # 链尽失败：按 P0-1 mode 处理——gate reject → 30005；否则 30003
    detail = "; ".join(errors) or "无可用实现"
    if gate_cfg is not None and gate_cfg.enabled and gate_cfg.mode == "reject":
        raise QualityGateError(f"全部解析实现质量不达标（{detail}）")
    raise ParseError(f"{filename} 解析失败（链内 {len(specs)} 个实现均不可用）: {detail}")
