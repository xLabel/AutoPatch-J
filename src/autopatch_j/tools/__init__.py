from __future__ import annotations

from autopatch_j.scanners import JavaScanner
from autopatch_j.tools.base import Tool, ToolExecutionResult
from autopatch_j.tools.edit_tool import ApplySearchReplaceTool, PreviewSearchReplaceTool
from autopatch_j.tools.registry import ToolRegistry
from autopatch_j.tools.scan_java import ScanJavaTool


def build_tools(scanner: JavaScanner | None = None) -> list[Tool]:
    return [
        ScanJavaTool(scanner=scanner),
        PreviewSearchReplaceTool(),
        ApplySearchReplaceTool(),
    ]


ALL_TOOLS = build_tools()


def get_tool(name: str, tools: list[Tool] | None = None) -> Tool | None:
    for tool in tools or ALL_TOOLS:
        if tool.name == name:
            return tool
    return None


def build_tool_registry(scanner: JavaScanner | None = None) -> ToolRegistry:
    return ToolRegistry(build_tools(scanner=scanner))
