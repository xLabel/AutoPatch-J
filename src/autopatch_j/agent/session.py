from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from autopatch_j.core.project import (
    UnsafeRepoPathError,
    normalize_repo_path,
    resolve_repo_path,
    to_repo_relative_path,
)

if TYPE_CHECKING:
    from autopatch_j.core.review import ProjectArtifactStore
    from autopatch_j.core.project import SourceReader
    from autopatch_j.core.domain import IntentType
    from autopatch_j.core.memory import MemoryManager
    from autopatch_j.core.patching import SearchReplacePatchDraft, SearchReplacePatchEngine
    from autopatch_j.core.patching import PatchQualityVerifier
    from autopatch_j.core.project import SymbolIndex
    from autopatch_j.core.review import ReviewWorkspaceManager
    from autopatch_j.tools.contract import ToolExecutionResult


@dataclass
class AgentSession:
    """
    Agent 与 Tool 共享的运行上下文。

    这里保存的是一次 CLI 会话内需要共享的执行依赖和短期状态。长期状态
    由专门的 core service 负责，例如 workspace 由 ReviewWorkspaceManager 管理，
    普通问答记忆由 MemoryManager 管理。
    """

    repo_root: Path
    artifact_manager: ProjectArtifactStore
    workspace_manager: ReviewWorkspaceManager
    symbol_indexer: SymbolIndex
    patch_engine: SearchReplacePatchEngine
    code_fetcher: SourceReader
    patch_verifier: PatchQualityVerifier | None = None
    memory_manager: MemoryManager | None = None

    focus_paths: list[str] = field(default_factory=list)
    source_read_cache: dict[tuple[str, str, int | None], ToolExecutionResult] = field(default_factory=dict)
    patch_source_hint: str | None = None
    proposed_patch_draft: SearchReplacePatchDraft | None = None
    revised_patch_draft: SearchReplacePatchDraft | None = None
    code_explain_allow_symbol_search: bool = True

    def set_focus_paths(self, paths: list[str] | None) -> None:
        normalized: list[str] = []
        for path in paths or []:
            try:
                clean = to_repo_relative_path(self.repo_root, resolve_repo_path(self.repo_root, path))
            except UnsafeRepoPathError:
                continue
            if clean and clean not in normalized:
                normalized.append(clean)
        self.focus_paths = normalized

    def is_focus_locked(self) -> bool:
        return bool(self.focus_paths)

    def is_path_in_focus(self, path: str) -> bool:
        try:
            safe_path = to_repo_relative_path(self.repo_root, resolve_repo_path(self.repo_root, path))
        except UnsafeRepoPathError:
            return False
        if not self.focus_paths:
            return True
        return safe_path in self.focus_paths

    def normalize_repo_path(self, path: str) -> str:
        return normalize_repo_path(path)

    def fetch_cached_source_read(self, tool_name: str, path: str, line: int | None) -> ToolExecutionResult | None:
        key = (tool_name, self.normalize_repo_path(path), line)
        return self.source_read_cache.get(key)

    def persist_cached_source_read(
        self,
        tool_name: str,
        path: str,
        line: int | None,
        result: ToolExecutionResult,
    ) -> None:
        key = (tool_name, self.normalize_repo_path(path), line)
        self.source_read_cache[key] = result

    def set_proposed_patch_draft(self, draft: SearchReplacePatchDraft) -> None:
        self.proposed_patch_draft = draft

    def clear_proposed_patch_draft(self) -> None:
        self.proposed_patch_draft = None

    def pop_proposed_patch_draft(self) -> SearchReplacePatchDraft | None:
        draft = self.proposed_patch_draft
        self.proposed_patch_draft = None
        return draft

    def set_revised_patch_draft(self, draft: SearchReplacePatchDraft) -> None:
        self.revised_patch_draft = draft

    def pop_revised_patch_draft(self) -> SearchReplacePatchDraft | None:
        draft = self.revised_patch_draft
        self.revised_patch_draft = None
        return draft

    def build_memory_context(
        self,
        intent: IntentType,
        current_user_text: str,
    ) -> str:
        if self.memory_manager is None:
            return ""
        return self.memory_manager.build_prompt_context(intent, current_user_text)

    def build_memory_debug_summary(
        self,
        intent: IntentType,
        current_user_text: str,
    ) -> str:
        if self.memory_manager is None:
            return ""
        return self.memory_manager.build_prompt_context_debug_summary(intent, current_user_text)

    def append_memory_turn(
        self,
        intent: IntentType,
        user_text: str,
        answer: str,
        scope_paths: list[str] | None = None,
    ) -> None:
        if self.memory_manager is None:
            return
        self.memory_manager.append_recent_turn(
            intent=intent,
            user_text=user_text,
            assistant_text=answer,
            scope_paths=scope_paths,
        )

    def clear_cache(self, clear_memory: bool = False) -> None:
        self.source_read_cache.clear()
        self.patch_source_hint = None
        self.proposed_patch_draft = None
        self.revised_patch_draft = None
        self.code_explain_allow_symbol_search = True
        if clear_memory and self.memory_manager is not None:
            self.memory_manager.clear()
