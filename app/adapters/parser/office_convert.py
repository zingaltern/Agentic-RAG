"""旧版 Office 外部转换（DEVELOPMENT.md 16.2）：LibreOffice soffice 无 shell 调用。

只负责「.doc → .docx」「.ppt → .pptx」的字节转换（临时目录、超时、用后清理）；
解析仍复用 office.py 的现代格式实现。引擎缺失/关闭/超时 → ParseError（30003）。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.errors import ParseError


def _find_soffice(cfg: object | None) -> str | None:
    """定位 LibreOffice 可执行文件：配置指定优先，其次 PATH 上的 soffice/libreoffice。"""
    explicit = str(getattr(cfg, "soffice_path", "") or "").strip() if cfg is not None else ""
    if explicit:
        return explicit if Path(explicit).exists() else None
    return shutil.which("soffice") or shutil.which("libreoffice")

def _convert_legacy_office(raw: bytes, source_ext: str, target_ext: str,
                           config: object | None) -> bytes:
    """16.2：LibreOffice 把 .doc/.ppt 转成 .docx/.pptx（临时目录、无 shell、带超时）。

    引擎缺失/关闭/超时/无产出 → ParseError（30003），错误文案含安装指引。
    """
    cfg = getattr(config, "office_convert", None)
    if cfg is not None and not bool(getattr(cfg, "enabled", True)):
        raise ParseError(
            f"旧版 Office 解析已关闭（ingestion.office_convert.enabled=false），无法解析 {source_ext} 文件")
    timeout = int(getattr(cfg, "timeout_seconds", 60)) if cfg is not None else 60
    soffice = _find_soffice(cfg)
    if not soffice:
        explicit = str(getattr(cfg, "soffice_path", "") or "").strip() if cfg is not None else ""
        reason = (f"配置的 ingestion.office_convert.soffice_path 不存在：{explicit!r}"
                  if explicit else "未检测到 soffice 命令")
        raise ParseError(
            f"解析旧版 Office（{source_ext}）需要 LibreOffice，{reason}。"
            "安装：macOS `brew install --cask libreoffice`；"
            "Debian/Ubuntu `apt-get install libreoffice`；"
            "或在 configs/settings.yaml 的 ingestion.office_convert.soffice_path 指定可执行文件路径")
    with tempfile.TemporaryDirectory(prefix="rag-office-") as tmp:
        tmp_dir = Path(tmp)
        src = tmp_dir / f"input{source_ext}"
        src.write_bytes(raw)
        out_dir = tmp_dir / "out"
        out_dir.mkdir()
        cmd = [soffice, "--headless", "--norestore", "--nolockcheck",
               f"-env:UserInstallation=file://{tmp_dir / 'profile'}",
               "--convert-to", target_ext.lstrip("."),
               "--outdir", str(out_dir), str(src)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ParseError(f"LibreOffice 转换超时（>{timeout}s）：{source_ext} 文件") from exc
        except OSError as exc:
            raise ParseError(f"LibreOffice 调用失败（{source_ext}）: {exc}") from exc
        produced = sorted(out_dir.glob(f"*{target_ext}"))
        if not produced:
            detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()[:200]
            raise ParseError(
                f"LibreOffice 转换未产出 {target_ext}（exit={proc.returncode}，{source_ext}）: {detail}")
        return produced[0].read_bytes()
