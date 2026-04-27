from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ToolResult:
    """工具执行结果的通用契约"""
    status: str
    message: str
    payload: Any = None


class ToolContext(Protocol):
    """
    工具环境契约 (Duck Typing)
    通过 Protocol 声明工具所需的环境能力，彻底斩断对 Agent 类的物理依赖。
    """
    repo_root: Path
    artifacts: Any
    symbol_indexer: Any
    patch_engine: Any
    fetcher: Any
    patch_verifier: Any
    focus_paths: list[str]
    patch_source_hint: str | None

    def is_focus_locked(self) -> bool: ...
    def is_path_in_focus(self, path: str) -> bool: ...
    def fetch_cached_source_read(self, path: str, symbol: str | None, line: int | None) -> ToolResult | None: ...
    def persist_cached_source_read(self, path: str, symbol: str | None, line: int | None, result: ToolResult) -> None: ...


class Tool:
    """
    工具基类 (Adapter Layer)
    职责：定义所有供 LLM 调用的工具必须遵循的接口。
    """
    name: str
    description: str
    parameters: dict[str, Any]

    def __init__(self, context: ToolContext | None = None) -> None:
        self.context = context

    def execute(self, **kwargs: Any) -> ToolResult:
        """执行逻辑，子类通过 self.context 访问服务，不再通过入参传递"""
        raise NotImplementedError

