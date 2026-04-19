from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from autopatch_j.scanners import ScanResult
from autopatch_j.tools.base import Tool, ToolExecutionResult
from autopatch_j.tools.edit_tool import EditPreview


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def execute(self, repo_root: Path, tool_name: str, tool_args: dict[str, Any]) -> ToolExecutionResult:
        tool = self._tools.get(tool_name)
        if tool is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                status="error",
                message=f"Unsupported tool: {tool_name}",
            )

        payload = tool.execute(repo_root=repo_root, **tool_args)
        if isinstance(payload, ScanResult | EditPreview):
            return ToolExecutionResult(
                tool_name=tool_name,
                status=payload.status,
                message=payload.message,
                payload=payload,
            )

        return ToolExecutionResult(
            tool_name=tool_name,
            status="ok",
            message="Tool executed.",
            payload=payload,
        )
