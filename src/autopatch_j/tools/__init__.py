from __future__ import annotations

from pathlib import Path
from typing import Any

from autopatch_j.scanners import JavaScanner
from autopatch_j.tools.base import Tool, ToolExecutionResult, ToolName
from autopatch_j.tools.edit import ApplySearchReplaceTool, PreviewSearchReplaceTool
from autopatch_j.tools.scan import ScanTool


def build_tools(scanner: JavaScanner | None = None) -> list[Tool]:
    return [
        ScanTool(scanner=scanner),
        PreviewSearchReplaceTool(),
        ApplySearchReplaceTool(),
    ]


ALL_TOOLS = build_tools()


def get_tool(name: ToolName, tools: list[Tool] | None = None) -> Tool | None:
    for tool in tools or ALL_TOOLS:
        if tool.name == name:
            return tool
    return None


def execute_tool(
    repo_root: Path,
    tool_name: ToolName,
    tool_args: dict[str, Any],
    tools: list[Tool] | None = None,
) -> ToolExecutionResult:
    tool = get_tool(tool_name, tools=tools)
    if tool is None:
        return ToolExecutionResult(
            tool_name=tool_name,
            status="error",
            message=f"Unsupported tool: {tool_name}",
        )
    return tool.execute(repo_root=repo_root, **tool_args)
