"""解析前安全预检（DEVELOPMENT.md 5.1 / S2）：魔数校验 + zip 炸弹防御。

在真正解析前拒绝「伪造扩展名 / 损坏文件 / 解压炸弹」，统一抛 ParseError（30003）。
"""
from __future__ import annotations

from app.adapters.parser.formats import LEGACY_OFFICE_EXTENSIONS, _OLE2_MAGIC
from app.core.errors import ParseError


def _check_magic(ext: str, raw: bytes, filename: str, config: object | None = None) -> None:
    """S2（V5.1）：上传前按魔数校验真实格式与扩展名一致（pdf %PDF；zip 系 PK\x03\x04；
    OLE2 系 .doc/.xls/.ppt；文本类嗅探）。

    不一致抛 ParseError（伪造扩展名/损坏文件统一语义）。
    """
    if ext == ".pdf":
        if not raw.startswith(b"%PDF"):
            raise ParseError(f"文件格式与扩展名不符（非 PDF）: {filename!r}")
    elif ext in (".docx", ".xlsx", ".pptx"):
        if not raw.startswith(b"PK\x03\x04"):
            raise ParseError(f"文件格式与扩展名不符（非 Office zip）: {filename!r}")
        _check_zip_bomb(raw, filename, config)
    elif ext in LEGACY_OFFICE_EXTENSIONS:
        # 16.2：旧版 Office 一律为 OLE2 复合文档（RTF/HTML 冒充的 .doc 不在支持范围）
        if not raw.startswith(_OLE2_MAGIC):
            raise ParseError(f"文件格式与扩展名不符（非旧版 Office OLE2）: {filename!r}")
    else:  # .md/.txt/代码/JSON/JSONL：文本嗅探（含 NUL 视为二进制冒充）
        sample = raw[:512]
        if sample and b"\x00" in sample:
            raise ParseError(f"文件格式与扩展名不符（疑似二进制冒充文本）: {filename!r}")

def _check_zip_bomb(raw: bytes, filename: str, config: object | None = None) -> None:
    """S2（V5.1）：zip 解压炸弹防御 —— 解压后总大小/条目数上限（默认 200MB/1000 条）。"""
    import io as _io
    import zipfile as _zipfile

    total_mb = int(getattr(config, "zip_max_total_mb", 200)) if config is not None else 200
    max_entries = int(getattr(config, "zip_max_entries", 1000)) if config is not None else 1000
    try:
        with _zipfile.ZipFile(_io.BytesIO(raw)) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ParseError(f"zip 条目数超过上限（{max_entries}）: {filename!r}")
            total = sum(info.file_size for info in infos)
            if total > total_mb * 1024 * 1024:
                raise ParseError(f"zip 解压总大小超过上限（{total_mb}MB）: {filename!r}")
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"zip 校验失败: {filename!r}（{exc}）") from exc
