"""联网检索适配器（A5，智能体体验增强）：Tavily Search，纯 HTTP（urllib，零新依赖）。

- key 只走环境变量（WebConfig.api_key_env，默认 RAG_TAVILY_API_KEY），禁止写盘。
- 失败/超时/无 key → 记录日志并返回空（不阻塞主链路）。
- 结果转 ScoredChunk：notion="web"，chunk.text 为纯净正文（**不含【网页】前缀/URL**，
  避免污染后续嵌入与幻觉检测）；来源信息（url/title）放 metadata.source/title，
  source_type="web"，供前端区分「网页 / 知识库」来源。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.core.domain import Chunk, ScoredChunk
from app.core.logging import get_logger

logger = get_logger("web_retriever")

TAVILY_ENDPOINT = "https://api.tavily.com/search"


class WebRetriever:
    def __init__(self, api_key_env: str = "RAG_TAVILY_API_KEY",
                 max_results: int = 5, search_depth: str = "basic",
                 timeout_seconds: int = 8, **_: Any):
        self.api_key_env = api_key_env
        self.max_results = max_results
        self.search_depth = search_depth
        self.timeout_seconds = timeout_seconds

    def retrieve(self, query: str) -> list[ScoredChunk]:
        import os

        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            logger.info("web 检索跳过：未配置 %s 环境变量", self.api_key_env)
            return []
        payload = {"api_key": api_key, "query": query,
                   "max_results": self.max_results, "search_depth": self.search_depth}
        req = urllib.request.Request(
            TAVILY_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            logger.warning("web 检索失败（网络）: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("web 检索失败: %s", exc)
            return []
        out: list[ScoredChunk] = []
        for idx, item in enumerate(body.get("results") or []):
            content = (item.get("content") or "").strip()
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not content:
                continue
            out.append(ScoredChunk(
                chunk=Chunk(
                    chunk_id=f"web_{idx}", document_id="web",
                    text=content[:800],
                    order=idx, parent_id=None,
                    metadata={"source": url or title, "source_type": "web", "title": title}),
                score=float(item.get("score") or 0.0),
                notion="web",
            ))
        return out
