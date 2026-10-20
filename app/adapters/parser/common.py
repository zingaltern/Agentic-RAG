"""解析统一化层（DEVELOPMENT.md 5.1）：所有解析器共用的产出与解码。

- `_build_document`：把 (文件名, 原始字节, 文本, metadata) 统一产出 `Document`
  —— document_id / content_hash / text_stats / language 等公共字段在此集中处理；
- `_decode_text`：文本类解码回退链（utf-8-sig → gbk → replace）。

本层只做「统一化」，不做清洗（清洗归 `app/adapters/cleaner/`）。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from app.adapters.parser.formats import CODE_LANGUAGES
from app.core.domain import Document


def _build_document(filename: str, raw: bytes, text: str, metadata: dict) -> Document:
    """按 2.3 约定生成 document_id（uuid4 hex 前 12 位）与 content_hash（sha256 原始字节）。

    content_hash 写入 metadata：Chunk 继承后可用于幂等去重检索（W2 5.10）。
    P0-1 探针：garbled_ratio（U+FFFD 乱码占比）在此对全部解析器统一计算——
    txt/html/eml 的 errors="replace" 解码同样产生 U+FFFD，必须纳入质量门禁视野；
    text_stats 缺失时（非 PDF 解析器）按单页文档补齐。
    """
    content_hash = hashlib.sha256(raw).hexdigest()
    metadata = {
        "source": filename,
        "kind": Path(filename).suffix.lstrip(".").lower(),
        "content_hash": content_hash,
        **metadata,
    }
    # 16.1：代码/配置类文件按扩展名补语言标识（解析器自身不感知文件名）
    ext = Path(filename).suffix.lower()
    if ext in CODE_LANGUAGES:
        metadata.setdefault("language", CODE_LANGUAGES[ext])
    stats = metadata.setdefault("text_stats", {
        "total_chars": len(text),
        "page_count": 1,
        "pages_with_text": 1 if text.strip() else 0,
    })
    garbled = text.count("\ufffd")
    stats["garbled_ratio"] = (garbled / len(text)) if text else 0.0
    return Document(
        document_id=uuid4().hex[:12],
        filename=filename,
        title=Path(filename).stem,
        content_hash=content_hash,
        text=text,
        metadata=metadata,
    )

def _decode_text(raw: bytes) -> str:
    """文本类解码回退链：utf-8-sig → gbk → utf-8(errors=replace)（16.1）。

    代码/JSON 常见 GBK 编码的中文注释；前两级严格解码失败才用替换字符兜底
    （替换字符计入 garbled_ratio，由质量门禁暴露）。
    """
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
