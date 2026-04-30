from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(slots=True)
class ToolResult:
    """LLM 工具调用返回给 Agent 的统一结果对象。"""
    status: str
    message: str
    summary: str | None = None
    payload: Any = None


class ToolContext(Protocol):
    """
    ReAct 工具运行时依赖契约。

    工具层只通过这个 Protocol 访问仓库、artifact、索引、补丁和缓存能力，
    避免直接依赖 Agent 或 CLI 流程类，便于单测中替换为轻量上下文对象。
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
    所有 LLM function call 工具的基类。

    子类负责声明 JSON schema 和执行逻辑；流程控制、入队和用户确认仍由
    Agent/Workflow 层决定，工具本身不直接修改用户源文件。
    """
    name: str
    description: str
    parameters: dict[str, Any]

    def __init__(self, context: ToolContext | None = None) -> None:
        self.context = context

    def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具逻辑，子类通过 self.context 访问运行时服务。"""
        raise NotImplementedError

