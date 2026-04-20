from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Callable

from autopatch_j.tools.base import ToolName


class AgentAction(StrEnum):
    ANSWER = "answer"
    SCAN = "scan"
    PATCH = "patch"


@dataclass(slots=True)
class AgentContext:
    user_text: str
    scoped_paths: list[str]
    has_active_findings: bool
    mention_context: str = "(none)"
    on_answer_delta: Callable[[str], None] | None = None


@dataclass(slots=True)
class AgentResult:
    action: AgentAction
    message: str
    tool_name: ToolName | None = None
    tool_args: dict[str, object] = field(default_factory=dict)
    streamed: bool = False
