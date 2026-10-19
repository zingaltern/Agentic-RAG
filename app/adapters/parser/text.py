"""纯文本类解析（DEVELOPMENT.md 5.1）：.md/.txt、HTML、邮件 .eml/.msg。"""
from __future__ import annotations

import re

from app.core.errors import ParseError


def _extract_markdown_sections(text: str) -> list[tuple[str, int]]:
    """提取 Markdown 章节标题（T5 元数据富化）：返回 [(title, 行号)]。"""
    sections: list[tuple[str, int]] = []
    for line_no, line in enumerate(text.split("\n")):
        m = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if m:
            sections.append((m.group(1).strip(), line_no))
    return sections

def _parse_text(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """txt/md：走统一解码回退链（utf-8-sig → gb18030 → replace，V5.2 修复 GBK 老文档乱码）。"""
    from app.adapters.parser.common import _decode_text

    text = _decode_text(raw)
    meta: dict = {"page": None, "author": None}
    sections = _extract_markdown_sections(text)
    if sections:
        meta["sections"] = sections        # T5：章节标题（title, 行号）
    return text, meta


def _parse_msg(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """Outlook .msg（OLE2 复合文档）：当前版本不支持解析，给出明确指引（V5.2）。

    白名单保留 .msg 的目的是给出比"格式不支持（40001）"更明确的错误与导出建议，
    而非静默失败或误报"二进制冒充文本"。
    """
    raise ParseError(
        "Outlook .msg 为 OLE2 二进制格式，当前版本不支持解析；"
        "请在 Outlook 中「另存为 → Internet 邮件(.eml)」后上传")

def _parse_html(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """HTML：标准库 html.parser 提取正文文本（去 script/style/注释），零新增依赖。"""
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        # V5.2：加入导航/页脚/表单等噪声容器（正文抽取更干净；内容不变的小改）
        SKIP_TAGS = {"script", "style", "noscript", "template",
                     "nav", "footer", "header", "aside", "form", "button", "svg", "iframe"}

        def __init__(self):
            super().__init__()
            self.out: list[str] = []
            self._skip_depth = 0
            self._block = False

        def handle_starttag(self, tag, attrs):
            if tag in self.SKIP_TAGS:
                self._skip_depth += 1
            if self._skip_depth == 0 and tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"):
                self._block = True

        def handle_endtag(self, tag):
            if tag in self.SKIP_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)
            if self._skip_depth == 0 and tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table"):
                self._block = True

        def handle_data(self, data):
            if self._skip_depth == 0 and data.strip():
                self.out.append(data.strip())

        def close(self):
            super().close()
            return "\n".join(self.out)

    try:
        text = raw.decode("utf-8", errors="replace")
        parser = _TextExtractor()
        parser.feed(text)
        body = parser.close()
    except Exception as exc:
        raise ParseError(f"HTML 解析失败: {exc}") from exc
    return body, {"page": None, "author": None}

def _parse_eml(raw: bytes, config: object | None = None) -> tuple[str, dict]:
    """邮件 .eml：标准库 email 解析，正文取 text/plain（无则 text/html 转文本）。"""
    import email
    from email import policy

    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
    except Exception as exc:
        raise ParseError(f"邮件解析失败（非 .eml）: {exc}") from exc
    subject = str(msg.get("subject") or "").strip()
    sender = str(msg.get("from") or "").strip()
    parts: list[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        if ctype == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace").strip())
        elif ctype == "text/html" and not parts:
            payload = part.get_payload(decode=True)
            if payload:
                html_text, _ = _parse_html(payload)
                if html_text:
                    parts.append(html_text)
    body = "\n\n".join(p for p in parts if p)
    if subject:
        body = f"主题：{subject}\n\n{body}".strip()
    return body, {"page": None, "author": sender or None}
