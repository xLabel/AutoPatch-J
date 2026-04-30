from __future__ import annotations

from autopatch_j.tools.base import Tool, ToolResult


class SymbolSearchTool(Tool):
    """
    Java 符号导航工具。

    查询 SymbolIndexer 产出的轻量索引，帮助 LLM 在大项目中定位类、方法或文件；
    它只返回候选位置，读取源码仍交给 read_source_code。
    """

    name = "search_symbols"
    description = "在当前项目中模糊搜索类名、方法名、接口或文件名。返回匹配项及其物理行号，帮助你快速定位代码。"
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词（如类名或方法名）。"}
        },
        "required": ["query"],
    }

    def execute(self, query: str) -> ToolResult:
        assert self.context is not None
        symbol_indexer = self.context.symbol_indexer
        results = symbol_indexer.search(query, limit=10)
        if self.context.is_focus_locked():
            results = [entry for entry in results if self.context.is_path_in_focus(entry.path)]

        if not results:
            return ToolResult(
                status="ok", 
                message=f"未找到与 '{query}' 相关的符号。",
                summary=f"未找到符号: {query}"
            )

        msg = f"为您找到以下与 '{query}' 相关的匹配项：\n"
        for i, entry in enumerate(results, 1):
            msg += f"{i}. [{entry.kind}] {entry.name} -> {entry.path}:{entry.line}\n"

        return ToolResult(
            status="ok", 
            message=msg, 
            summary=f"已定位符号: {query}",
            payload=[e.path for e in results]
        )
