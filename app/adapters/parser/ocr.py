"""OCR 预留接口（DEVELOPMENT.md 13.10 P3）：默认抛错，接入引擎只需替换实现。"""
from __future__ import annotations

from app.core.errors import ParseError


def _parse_ocr(raw: bytes, ext: str) -> tuple[str, dict]:
    """图片 OCR 预留接口（DEVELOPMENT.md 13.10 P3：parser.ocr.enabled=false 默认关）。

    未接入引擎时抛 ParseError（明确不可用，不静默返回空文档）；
    接入引擎（如 paddleocr/pytesseract）只需替换本函数内部实现。
    """
    raise ParseError(f"OCR 未启用（图片 {ext} 暂不支持，需开启 parser.ocr.enabled 并接入引擎）")

def _parse_ocr_entry(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """OCR 占位（13.10 P3 预留）：适配单参签名，保持现抛错实现，不入默认链。"""
    return _parse_ocr(raw, ".pdf")
