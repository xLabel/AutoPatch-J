from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from autopatch_j.core.service_context import ServiceContext


@dataclass(slots=True)
class ToolResult:
    """工具执行结果的通用契约"""
    status: str
    message: str
    payload: Any = None


class Tool:
    """
    工具基类 (Adapter Layer)
    职责：定义所有供 LLM 调用的工具必须遵循的接口。
    """
    name: str
    description: str
    parameters: dict[str, Any]

    def execute(self, context: ServiceContext, **kwargs: Any) -> ToolResult:
        raise NotImplementedError
