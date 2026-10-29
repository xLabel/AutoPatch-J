"""业务工具的执行上下文和观察结果；schema 由 SDK 生成。"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import ClassVar
from typing import Protocol

import pydantic

from autopatch_j.core.domain import audit
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import types
from autopatch_j.core.patching import verification
from autopatch_j.core.project import source_reader
from autopatch_j.core.project import symbol_index
from autopatch_j.core.review import artifacts
from autopatch_j.core.review import workspace


@dataclasses.dataclass(slots=True)
class ToolExecutionResult:
    """工具的业务观察，不是模型消息或队列提交记录。"""

    status: str
    message: str
    summary: str | None = None
    payload: dict[str, pydantic.JsonValue] = dataclasses.field(
        default_factory=dict
    )
    name: str = ""


class ToolRuntimeContext(Protocol):
    """源码工具使用的业务服务和请求状态。"""

    repo_root: pathlib.Path
    artifact_manager: artifacts.ProjectArtifactStore
    workspace_manager: workspace.ReviewWorkspaceManager
    symbol_indexer: symbol_index.SymbolIndex
    patch_engine: search_replace.SearchReplacePatchEngine
    code_fetcher: source_reader.SourceReader
    patch_verifier: verification.PatchQualityVerifier | None
    current_finding: audit.FindingTask | None
    focus_paths: list[str]
    patch_source_hint: str | None

    def is_focus_locked(self) -> bool:
        """当前请求是否有明确锁定的源码文件集合。"""
        ...

    def is_path_in_focus(self, path: str) -> bool:
        """真实目标必须安全且位于当前焦点。"""
        ...

    def normalize_repo_path(self, path: str) -> str:
        """统一相对路径表示，不代替实际准入。"""
        ...

    def fetch_cached_source_read(
        self, tool_name: str, path: str, line: int | None
    ) -> ToolExecutionResult | None:
        """返回同一请求内、相同工具和目标的缓存观察。"""
        ...

    def persist_cached_source_read(
        self,
        tool_name: str,
        path: str,
        line: int | None,
        result: ToolExecutionResult,
    ) -> None:
        """保存当前请求内的观察，不能用作可写源码证明。"""
        ...

    def set_proposed_patch_draft(
        self, draft: types.SearchReplacePatchDraft
    ) -> None:
        """暂存新草案，不提交审核队列。"""
        ...

    def clear_proposed_patch_draft(self) -> None:
        """丢弃尚未移交工作流的新草案。"""
        ...

    def set_revised_patch_draft(
        self, draft: types.SearchReplacePatchDraft
    ) -> None:
        """暂存当前审核项的修订，不替换原审核项。"""
        ...


@dataclasses.dataclass
class BusinessTool:
    """持有业务上下文的本地处理器，不执行模型派发或生成 schema。"""

    context: ToolRuntimeContext
    name: ClassVar[str] = ""
