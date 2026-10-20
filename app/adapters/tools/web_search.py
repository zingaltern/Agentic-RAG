"""web_search 工具（DEVELOPMENT.md 17.4，默认关）：Tavily 联网检索。

- 只有 orchestration.agent.tools.web_search.enabled 且请求 web_search=true 时进入 allowlist；
- 查询会发往第三方（Tavily），文档正文不会发；结果标记 is_web，KB 优先；
- 域名 allowlist 与 WebRetriever 的私网拦截共同生效。
"""
from __future__ import annotations

import hashlib
import ipaddress
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field

from app.core.domain import Evidence, ToolContext, ToolResult
from app.core.interfaces import Tool


def is_public_web_url(url: str) -> bool:
    """只允许 http/https 公网地址；仅做**字面量**防护。

    - 拒绝 localhost/.local/.internal 与私网/环回/链路本地/reserved IP 字面量；
    - 不做 DNS 解析：127.0.0.1.nip.io 这类解析型内网地址不在本函数拦截范围，
      必须依赖域名 allowlist 与出网代理/防火墙；
    - V1 不主动抓取网页（内容来自 Tavily），此校验是纵深防御，防止后续
      增加"打开原文"能力时引入 SSRF。
    """
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True
    return not (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _normalize_web_url(url: str) -> str:
    """证据 ID 的 URL 规范化：scheme+host+path，忽略 query/fragment/默认端口。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    scheme = parsed.scheme.lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    netloc = host
    if port and not ((scheme == "http" and port == 80)
                     or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    return urlunparse((scheme, netloc, parsed.path or "/", "", "", ""))


def _normalize_domain(value: str) -> str:
    raw = str(value or "").strip().lower()
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    return raw.lstrip(".").rstrip(".").strip()


class WebSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=300)
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchTool(Tool):
    name = "web_search"
    description = (
        "在外部网页中检索（第三方服务）。仅当知识库没有依据、且用户明确允许联网时使用；"
        "返回结果标记为外部来源，与知识库冲突时以知识库为准。"
    )
    args_model = WebSearchArgs
    timeout_ms = 9000
    max_result_chars = 4000
    side_effect = "external"

    def __init__(self, web_retriever: Any = None, max_results: int = 5,
                 allowed_domains: list[str] | None = None, **_: Any):
        self.web_retriever = web_retriever
        self.max_results = max(1, max_results)
        self.allowed_domains = sorted({
            d for d in (_normalize_domain(item) for item in (allowed_domains or []))
            if d
        })

    def _host_allowed(self, url: str) -> bool:
        """域名边界匹配：example.com 允许 example.com 与 *.example.com，
        但拒绝 example.com.evil.org / notexample.com。"""
        if not self.allowed_domains:
            return False
        try:
            host = (urlparse(url or "").hostname or "").lower().rstrip(".")
        except ValueError:
            return False
        if not host:
            return False
        return any(host == d or host.endswith("." + d) for d in self.allowed_domains)

    def invoke(self, ctx: ToolContext, args: WebSearchArgs) -> ToolResult:
        if not ctx.web_search or self.web_retriever is None:
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary="联网检索未启用",
                              error_code="TOOL_SCOPE_DENIED")
        if not self.allowed_domains:
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary="web_search 未配置域名白名单，已拒绝联网",
                              error_code="TOOL_SCOPE_DENIED",
                              error_message="agent.tools.web_search.allowed_domains 不能为空")
        try:
            hits = self.web_retriever.retrieve(args.query)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(call_id="", name=self.name, ok=False,
                              summary=f"联网检索失败：{type(exc).__name__}",
                              error_code="TOOL_EXECUTION_ERROR", retryable=True)
        evidence: list[Evidence] = []
        for idx, item in enumerate(hits[: max(1, min(args.max_results, self.max_results))]):
            meta = item.chunk.metadata or {}
            url = str(meta.get("source") or "")
            if not url:
                continue
            if not is_public_web_url(url):
                continue
            if not self._host_allowed(url):
                continue
            web_id = "web_" + hashlib.sha1(
                _normalize_web_url(url).encode("utf-8")).hexdigest()[:12]
            evidence.append(Evidence(
                chunk_id=web_id,
                document_id="web",
                text=item.chunk.text,
                source=str(meta.get("title") or url or "网页"),
                page=None,
                section=None,
                score=float(item.score),
                retrieval_query=args.query,
                tool_name=self.name,
                is_web=True,
                metadata={"url": url, "source_type": "web"},
            ))
        return ToolResult(call_id="", name=self.name, ok=True,
                          summary=(f"联网检索到 {len(evidence)} 条结果"
                                   if evidence else "联网检索无结果"),
                          data={"query": args.query, "count": len(evidence)},
                          evidence=evidence)
