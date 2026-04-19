from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    def execute(self, repo_root: Path, **kwargs: Any) -> object:
        raise NotImplementedError


@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: str
    status: str
    message: str
    payload: object | None = None
