from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from autopatch_j.tools.edit_tool import EditPreview, SearchReplaceEdit, apply_search_replace, preview_search_replace
from autopatch_j.tools.scan_java import ScanResult, scan_java

ToolHandler = Callable[..., object]


@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: str
    status: str
    message: str
    payload: object | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {
            "apply_search_replace": self._apply_search_replace,
            "preview_search_replace": self._preview_search_replace,
            "scan_java": self._scan_java,
        }

    def execute(self, repo_root: Path, tool_name: str, tool_args: dict[str, Any]) -> ToolExecutionResult:
        handler = self._handlers.get(tool_name)
        if handler is None:
            return ToolExecutionResult(
                tool_name=tool_name,
                status="error",
                message=f"Unsupported tool: {tool_name}",
            )

        payload = handler(repo_root=repo_root, **tool_args)
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

    def _scan_java(self, repo_root: Path, scope: list[str]) -> ScanResult:
        return scan_java(repo_root, scope)

    def _preview_search_replace(
        self,
        repo_root: Path,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> EditPreview:
        return preview_search_replace(
            repo_root,
            SearchReplaceEdit(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            ),
        )

    def _apply_search_replace(
        self,
        repo_root: Path,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> EditPreview:
        return apply_search_replace(
            repo_root,
            SearchReplaceEdit(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            ),
        )
