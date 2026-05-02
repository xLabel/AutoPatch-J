from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopatch_j.core.artifact_manager import ArtifactManager
    from autopatch_j.core.code_fetcher import CodeFetcher
    from autopatch_j.core.models import IntentType
    from autopatch_j.core.memory import MemoryManager
    from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
    from autopatch_j.core.patch_verifier import PatchVerifier
    from autopatch_j.core.symbol_indexer import SymbolIndexer
    from autopatch_j.core.workspace_manager import WorkspaceManager
    from autopatch_j.tools.base import ToolResult


@dataclass
class AgentSession:
    """
    Agent 与 Tool 共享的运行上下文。

    这里保存的是一次 CLI 会话内需要共享的执行依赖和短期状态。长期状态
    由专门的 core service 负责，例如 workspace 由 WorkspaceManager 管理，
    普通问答记忆由 MemoryManager 管理。
    """

    repo_root: Path
    artifact_manager: ArtifactManager
    workspace_manager: WorkspaceManager
    symbol_indexer: SymbolIndexer
    patch_engine: PatchEngine
    code_fetcher: CodeFetcher
    patch_verifier: PatchVerifier | None = None
    memory_manager: MemoryManager | None = None

    focus_paths: list[str] = field(default_factory=list)
    source_read_cache: dict[tuple[str, str | None, int | None], ToolResult] = field(default_factory=dict)
    patch_source_hint: str | None = None
    proposed_patch_draft: PatchDraft | None = None
    revised_patch_draft: PatchDraft | None = None
    code_explain_allow_symbol_search: bool = True
    action_history: list[str] = field(default_factory=list)

    def set_focus_paths(self, paths: list[str] | None) -> None:
        normalized: list[str] = []
        for path in paths or []:
            clean = self.normalize_repo_path(path)
            if clean and clean not in normalized:
                normalized.append(clean)
        self.focus_paths = normalized

    def is_focus_locked(self) -> bool:
        return bool(self.focus_paths)

    def is_path_in_focus(self, path: str) -> bool:
        if not self.focus_paths:
            return True
        return self.normalize_repo_path(path) in self.focus_paths

    def normalize_repo_path(self, path: str) -> str:
        clean = path.replace("\\", "/").strip()
        if clean.startswith("./"):
            clean = clean[2:]
        return clean

    def fetch_cached_source_read(self, path: str, symbol: str | None, line: int | None) -> ToolResult | None:
        key = (self.normalize_repo_path(path), symbol, line)
        return self.source_read_cache.get(key)

    def persist_cached_source_read(self, path: str, symbol: str | None, line: int | None, result: ToolResult) -> None:
        key = (self.normalize_repo_path(path), symbol, line)
        self.source_read_cache[key] = result

    def set_proposed_patch_draft(self, draft: PatchDraft) -> None:
        self.proposed_patch_draft = draft

    def clear_proposed_patch_draft(self) -> None:
        self.proposed_patch_draft = None

    def pop_proposed_patch_draft(self) -> PatchDraft | None:
        draft = self.proposed_patch_draft
        self.proposed_patch_draft = None
        return draft

    def set_revised_patch_draft(self, draft: PatchDraft) -> None:
        self.revised_patch_draft = draft

    def pop_revised_patch_draft(self) -> PatchDraft | None:
        draft = self.revised_patch_draft
        self.revised_patch_draft = None
        return draft

    def record_action(self, action_fingerprint: str) -> None:
        self.action_history.append(action_fingerprint)
        if len(self.action_history) > 10:
            self.action_history.pop(0)

    def is_stuck_in_loop(self) -> bool:
        if len(self.action_history) >= 3:
            return self.action_history[-1] == self.action_history[-2] == self.action_history[-3]
        return False

    def build_memory_context(
        self,
        intent: IntentType,
        current_user_text: str,
    ) -> str:
        if self.memory_manager is None:
            return ""
        return self.memory_manager.build_prompt_context(intent, current_user_text)

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
        self.action_history.clear()
