from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ToolResult:
    """工具执行结果的通用契约"""
    status: str
    message: str
    summary: str | None = None
    payload: Any = None


class ToolContext(Protocol):
    """
    工具环境契约 (Duck Typing Protocol)。
    核心架构设计：通过 Protocol (鸭子类型) 声明 Tool 执行时所需的底层服务依赖，
    而不是直接导入 Agent 或 WorkspaceManager 等实体类。
    这一设计彻底斩断了 Tool 层与 Agent 层/流程层的双向循环依赖，保障了工具链的独立拓展性与单元测试的可行性。
    """
    repo_root: Path
    artifact_manager: Any
    workspace_manager: Any
    symbol_indexer: Any
    patch_engine: Any
    code_fetcher: Any
    patch_verifier: Any
    focus_paths: list[str]
    patch_source_hint: str | None

    def is_focus_locked(self) -> bool: ...
    def is_path_in_focus(self, path: str) -> bool: ...
    def fetch_cached_source_read(self, path: str, symbol: str | None, line: int | None) -> ToolResult | None: ...
    def persist_cached_source_read(self, path: str, symbol: str | None, line: int | None, result: ToolResult) -> None: ...
    def set_proposed_patch_draft(self, draft: Any) -> None: ...
    def clear_proposed_patch_draft(self) -> None: ...
    def set_revised_patch_draft(self, draft: Any) -> None: ...


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

