from __future__ import annotations

from typing import TYPE_CHECKING
from autopatch_j.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from autopatch_j.agent.agent import AutoPatchAgent


class SymbolSearchTool(Tool):
    """
    符号搜索工具 (Navigator)
    职责：基于索引快速定位项目中的符号位置。
    """
    name = "search_symbols"
    description = "在当前项目中模糊搜索类名、方法名、接口或文件名。返回匹配项及其物理行号，帮助你快速定位代码。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词（如类名或方法名）。"}
        },
        "required": ["query"]
    }

    def execute(self, query: str) -> ToolResult:
        assert self.context is not None
        results = self.context.indexer.search(query, limit=10)
        
        if not results:
            return ToolResult(status="ok", message=f"未找到与 '{query}' 相关的符号。")

        msg = f"为您找到以下与 '{query}' 相关的匹配项：\n"
        for i, entry in enumerate(results, 1):
            msg += f"{i}. [{entry.kind}] {entry.name} -> {entry.path}:{entry.line}\n"
        
        return ToolResult(status="ok", message=msg, payload=[e.path for e in results])
