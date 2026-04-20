from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ToolName(StrEnum):
    SCAN = "scan"
    PREVIEW_SEARCH_REPLACE = "preview_search_replace"
    APPLY_SEARCH_REPLACE = "apply_search_replace"


@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: ToolName | str
    status: str
    message: str
    payload: object | None = None


class Tool:
    name: ToolName
    description: str = ""
    parameters: dict[str, Any] = {}

    def execute(self, repo_root: Path, **kwargs: Any) -> ToolExecutionResult:
        raise NotImplementedError
