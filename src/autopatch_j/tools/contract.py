from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from autopatch_j.tools.names import FunctionToolName


@dataclass(frozen=True, slots=True)
class FunctionToolSpec:
    """LLM function_call 可见的工具声明。"""

    name: FunctionToolName
    description: str
    parameters: dict[str, Any]

    @property
    def json_name(self) -> str:
        return self.name.value


@dataclass(slots=True)
class ToolExecutionResult:
    """本地工具执行后回写给 ReAct 循环的统一结果。"""

    status: str
    message: str
    summary: str | None = None
    payload: Any = None


class ToolRuntimeContext(Protocol):
    """
    ReAct 工具运行时依赖契约。

    工具层只通过这个 Protocol 访问仓库、artifact、索引、补丁和缓存能力，
    避免直接依赖 Agent 或 CLI 流程类，便于单测替换为轻量上下文对象。
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
    def fetch_cached_source_read(self, path: str, symbol: str | None, line: int | None) -> ToolExecutionResult | None: ...
    def persist_cached_source_read(
        self,
        path: str,
        symbol: str | None,
        line: int | None,
        result: ToolExecutionResult,
    ) -> None: ...
    def set_proposed_patch_draft(self, draft: Any) -> None: ...
    def clear_proposed_patch_draft(self) -> None: ...
    def set_revised_patch_draft(self, draft: Any) -> None: ...


class FunctionTool:
    """
    所有 LLM function_call 工具的基类。

    子类只负责声明 schema 和执行本地能力；流程推进、入队和用户确认由上层 workflow 决定。
    """

    spec: ClassVar[FunctionToolSpec]

    def __init__(self, context: ToolRuntimeContext | None = None) -> None:
        self.context = context

    @property
    def name(self) -> str:
        return self.spec.json_name

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def parameters(self) -> dict[str, Any]:
        return self.spec.parameters

    def require_context(self) -> ToolRuntimeContext:
        if self.context is None:
            raise RuntimeError(f"工具 {self.name} 缺少运行时上下文。")
        return self.context

    def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行工具逻辑，子类通过 self.context 访问运行时服务。"""
        raise NotImplementedError
