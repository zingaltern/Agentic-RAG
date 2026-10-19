"""解析前安全预检（DEVELOPMENT.md 5.1 / S2）：魔数校验 + zip 炸弹防御。

在真正解析前拒绝「伪造扩展名 / 损坏文件 / 解压炸弹」，统一抛 ParseError（30003）。
"""
from __future__ import annotations

import re

from app.adapters.parser.formats import LEGACY_OFFICE_EXTENSIONS, _OLE2_MAGIC
from app.core.errors import ParseError

_ENTITY_MARK = re.compile(rb"<!ENTITY", re.IGNORECASE)


def _check_magic(ext: str, raw: bytes, filename: str, config: object | None = None) -> None:
    """S2（V5.1）：上传前按魔数校验真实格式与扩展名一致（pdf %PDF；zip 系 PK\x03\x04；
    OLE2 系 .doc/.xls/.ppt/.msg；文本类嗅探）。

    不一致抛 ParseError（伪造扩展名/损坏文件统一语义）。
    """
    if ext == ".pdf":
        if not raw.startswith(b"%PDF"):
            raise ParseError(f"文件格式与扩展名不符（非 PDF）: {filename!r}")
    elif ext in (".docx", ".xlsx", ".pptx"):
        if not raw.startswith(b"PK\x03\x04"):
            raise ParseError(f"文件格式与扩展名不符（非 Office zip）: {filename!r}")
        _check_zip_bomb(raw, filename, config)
        _check_ooxml_entities(raw, filename)   # V5.2：实体膨胀/XXE 防御
    elif ext in LEGACY_OFFICE_EXTENSIONS or ext == ".msg":
        # 16.2：旧版 Office 一律为 OLE2 复合文档（.msg 同为 OLE2，V5.2 单独校验）
        if not raw.startswith(_OLE2_MAGIC):
            raise ParseError(f"文件格式与扩展名不符（非 OLE2 复合文档）: {filename!r}")
    else:  # .md/.txt/代码/JSON/JSONL：文本嗅探（含 NUL 视为二进制冒充）
        sample = raw[:512]
        if sample and b"\x00" in sample:
            raise ParseError(f"文件格式与扩展名不符（疑似二进制冒充文本）: {filename!r}")


def _check_ooxml_entities(raw: bytes, filename: str) -> None:
    """V5.2：OOXML XML 部件实体膨胀（billion laughs/XXE）防御。

    python-docx/openpyxl/python-pptx 解析前扫描 zip 内 XML/rels 部件，
    含 `<!ENTITY` 声明直接拒绝（正常 OOXML 部件不含实体声明）。
    """
    import io as _io
    import zipfile as _zipfile

    max_part_bytes = 5 * 1024 * 1024      # 单部件 >5MB 视为异常
    max_scan_bytes = 32 * 1024 * 1024     # 扫描总量上限（防 CPU/内存开销）
    try:
        with _zipfile.ZipFile(_io.BytesIO(raw)) as zf:
            scanned = 0
            for info in zf.infolist():
                name = info.filename.lower()
                if not (name.endswith(".xml") or name.endswith(".rels")):
                    continue
                if info.file_size > max_part_bytes:
                    raise ParseError(f"OOXML 部件异常过大（{info.filename}），拒绝解析: {filename!r}")
                if scanned >= max_scan_bytes:
                    break
                data = zf.read(info.filename)
                scanned += len(data)
                if _ENTITY_MARK.search(data):
                    raise ParseError(
                        f"OOXML 含 XML 实体声明（疑似实体膨胀/XXE 攻击），拒绝解析: {filename!r}")
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError(f"OOXML 安全校验失败: {filename!r}（{exc}）") from exc

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
