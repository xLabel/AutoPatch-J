from __future__ import annotations

from autopatch_j.tools.contract import FunctionTool, FunctionToolSpec, ToolExecutionResult
from autopatch_j.tools.names import FunctionToolName


class SearchSymbolsTool(FunctionTool):
    """
    查询 Java 符号索引。

    只返回候选位置；需要源码内容时继续调用 read_source_code。
    """

    spec = FunctionToolSpec(
        name=FunctionToolName.SEARCH_SYMBOLS,
        description=(
            "在当前项目索引中搜索类名、方法名、接口或文件名，返回候选文件和行号。"
            "只用于定位代码位置，不读取源码内容。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要搜索的类名、方法名、接口名或文件名关键词。"}
            },
            "required": ["query"],
        },
    )

    def execute(self, query: str) -> ToolExecutionResult:
        context = self.require_context()
        symbol_indexer = context.symbol_indexer
        results = symbol_indexer.search(query, limit=10)
        if context.is_focus_locked():
            results = [entry for entry in results if context.is_path_in_focus(entry.path)]

        if not results:
            return ToolExecutionResult(
                status="ok",
                message=f"未找到与 '{query}' 相关的符号。",
                summary=f"未找到符号: {query}",
            )

        msg = f"为您找到以下与 '{query}' 相关的匹配项：\n"
        for i, entry in enumerate(results, 1):
            msg += f"{i}. [{entry.kind}] {entry.name} -> {entry.path}:{entry.line}\n"

        return ToolExecutionResult(
            status="ok",
            message=msg,
            summary=f"已定位符号: {query}",
            payload=[entry.path for entry in results],
        )
