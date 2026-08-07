"""JSON 结构化日志 + trace_id（DEVELOPMENT.md 2.3、10.2）。"""
from __future__ import annotations

import io
import json
import logging

from app.core.logging import JsonFormatter, set_trace_id, setup_logging


def test_json_formatter_includes_trace_id_and_extra(capsys):
    setup_logging(level="info", json=True)
    logger = logging.getLogger("app.test")
    set_trace_id("trace-xyz")
    logger.info("入库完成", extra={"extra_fields": {"chunk_count": 12}})
    captured = capsys.readouterr()
    payload = json.loads(captured.err.strip())
    assert payload["trace_id"] == "trace-xyz"
    assert payload["message"] == "入库完成"
    assert payload["chunk_count"] == 12


def test_plain_formatter_when_json_disabled(capsys):
    setup_logging(level="info", json=False)
    logger = logging.getLogger("app.test2")
    set_trace_id("trace-2")
    logger.warning("解析失败")
    out = capsys.readouterr().err
    assert "解析失败" in out
    assert "trace-2" in out


def test_get_trace_id_default():
    from app.core.logging import get_trace_id

    set_trace_id("t1")
    assert get_trace_id() == "t1"
