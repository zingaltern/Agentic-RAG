"""P-D 工具适配器（DEVELOPMENT.md 17.4）。

工具只读、声明式参数、统一注册；具体装配在 app/services/agent/runtime.py 完成，
避免适配器反向依赖服务层。
"""
from app.adapters.tools.ask_user import AskUserTool
from app.adapters.tools.kb_fetch import KbFetchTool
from app.adapters.tools.kb_list_documents import KbListDocumentsTool
from app.adapters.tools.kb_outline import KbOutlineTool
from app.adapters.tools.kb_search import KbSearchTool
from app.adapters.tools.registry import ToolRegistry, allowed_tool_names
from app.adapters.tools.web_search import WebSearchTool

__all__ = [
    "ToolRegistry", "allowed_tool_names",
    "KbSearchTool", "KbFetchTool", "KbListDocumentsTool",
    "KbOutlineTool", "WebSearchTool", "AskUserTool",
]
