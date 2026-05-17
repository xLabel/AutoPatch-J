from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from autopatch_j.tools.contract import FunctionTool, ToolRuntimeContext
from autopatch_j.tools.function_calls import (
    GetFindingDetailTool,
    ProposePatchTool,
    ReadSourceBlockTool,
    ReadSourceContextTool,
    ReadSourceFileTool,
    RevisePatchTool,
    SearchSymbolsTool,
)
from autopatch_j.tools.names import ToolNameLike, tool_name_value


class FunctionToolCatalog:
    """
    Agent 可用 function_call 工具的注册表。

    所有会传给 LLM 的工具都来自 tools.function_calls；catalog 只负责构造、查找和导出 schema。
    """

    def __init__(self, tools: Iterable[FunctionTool]) -> None:
        self.tools: dict[str, FunctionTool] = {tool.name: tool for tool in tools}

    @classmethod
    def for_context(cls, context: ToolRuntimeContext) -> FunctionToolCatalog:
        return cls(
            [
                GetFindingDetailTool(context),
                ReadSourceFileTool(context),
                ReadSourceBlockTool(context),
                ReadSourceContextTool(context),
                SearchSymbolsTool(context),
                ProposePatchTool(context),
                RevisePatchTool(context),
            ]
        )

    def get(self, name: ToolNameLike) -> FunctionTool | None:
        return self.tools.get(tool_name_value(name))

    def schemas(self, allowed_tool_names: Iterable[ToolNameLike]) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool_name in allowed_tool_names:
            tool = self.get(tool_name)
            if tool is None:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
            )
        return schemas
