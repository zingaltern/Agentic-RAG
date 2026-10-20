"""P-D 配置契约（DEVELOPMENT.md 17.6）：默认关闭/非法值启动即失败。"""
from __future__ import annotations

import copy

import pytest

from app.core.config import SystemConfig, load_config
from app.core.errors import SystemConfigError


def _payload():
    return copy.deepcopy(load_config().model_dump())


def test_agent_defaults_are_safe():
    cfg = load_config()
    assert cfg.orchestration.mode == "reflect"
    assert cfg.orchestration.agent.enabled is False
    assert cfg.orchestration.agent.max_steps == 8
    assert cfg.orchestration.agent.tools.kb_outline.enabled is False
    assert cfg.orchestration.agent.tools.web_search.enabled is False
    assert cfg.llm.tool_calling == "auto"


def test_invalid_mode_rejected():
    data = _payload()
    data["orchestration"]["mode"] = "deep"
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)


def test_invalid_tool_calling_rejected():
    data = _payload()
    data["llm"]["tool_calling"] = "magic"
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)


def test_web_tool_requires_web_enabled():
    data = _payload()
    data["orchestration"]["agent"]["tools"]["web_search"]["enabled"] = True
    data["web"]["enabled"] = False
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)


def test_web_tool_requires_explicit_allowlist():
    data = _payload()
    data["orchestration"]["agent"]["tools"]["web_search"]["enabled"] = True
    data["web"]["enabled"] = True
    data["orchestration"]["agent"]["tools"]["web_search"]["allowed_domains"] = []
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)


def test_agent_step_bounds():
    data = _payload()
    data["orchestration"]["agent"]["max_steps"] = 0
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)


def test_fallback_non_reflect_rejected():
    data = _payload()
    data["orchestration"]["agent"]["fallback"]["on_tool_error"] = "abstain"
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)


@pytest.mark.parametrize("path,value", [
    (("max_parallel_tools",), 0),
    (("max_parse_failures",), 6),
    (("max_context_tokens",), 0),
    (("max_evidence_items",), 0),
    (("max_evidence_chars",), 100),
    (("planning", "temperature"), 3),
    (("reflection", "min_evidence_per_subgoal"), 0),
    (("synthesis", "max_repair_attempts"), 2),
    (("routing", "max_escalations"), 2),
    (("tools", "kb_search", "default_k"), 0),
    (("tools", "kb_list_documents", "max_items"), 0),
])
def test_agent_numeric_bounds_rejected(path, value):
    data = _payload()
    node = data["orchestration"]["agent"]
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    with pytest.raises(SystemConfigError):
        SystemConfig(**data)
