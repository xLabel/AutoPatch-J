from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from autopatch_j.agent.agent import AutoPatchAgent


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

    def __init__(self, context: AutoPatchAgent | None = None) -> None:
        # 允许在初始化时注入上下文单例
        self.context = context

    def execute(self, **kwargs: Any) -> ToolResult:
        """执行逻辑，子类通过 self.context 访问服务，不再通过入参传递"""
        raise NotImplementedError

