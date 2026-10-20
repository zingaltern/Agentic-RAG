"""P-D Agent 运行层（预算/路由）；运行时请从 app.services.agent.runtime 导入。

注意：这里不做 `from ...runtime import AgentRuntime` 的急切导出——
adapters/agent 的实现会 import prompts，若包初始化再急切导入 runtime，
会形成 adapters → services → adapters 的循环。
"""
from app.services.agent.budget import budget_from_config, check_budget
from app.services.agent.router import complexity_score, route_decision

__all__ = ["budget_from_config", "check_budget", "complexity_score", "route_decision"]
