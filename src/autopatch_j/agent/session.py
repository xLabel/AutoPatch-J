from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autopatch_j.core.domain import IntentType
from autopatch_j.llm.options import LLMCallDiagnostic
from autopatch_j.core.project import (
    UnsafeRepoPathError,
    normalize_repo_path,
    resolve_repo_path,
    to_repo_relative_path,
)

if TYPE_CHECKING:
    from autopatch_j.core.review import ProjectArtifactStore
    from autopatch_j.core.project import SourceReader
    from autopatch_j.core.memory import MemoryManager
    from autopatch_j.core.patching import SearchReplacePatchDraft, SearchReplacePatchEngine
    from autopatch_j.core.patching import PatchQualityVerifier
    from autopatch_j.core.project import SymbolIndex
    from autopatch_j.core.review import ReviewWorkspaceManager
    from autopatch_j.tools.contract import ToolExecutionResult


MEMORY_ROUTING_CONTEXT_MAX_CHARS = 4_000
ORDINARY_MEMORY_INTENTS = {IntentType.CODE_EXPLAIN, IntentType.GENERAL_CHAT}


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
    memory_thread_id: str | None = None

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

    def bind_memory_thread(self, thread_id: str) -> None:
        self.memory_thread_id = thread_id

    def clear_memory_thread(self) -> None:
        self.memory_thread_id = None

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

    def build_thread_history(self, intent: IntentType) -> list[dict[str, Any]]:
        if self.memory_manager is None or intent not in ORDINARY_MEMORY_INTENTS:
            return []
        build_history = getattr(self.memory_manager, "build_thread_history", None)
        if build_history is None:
            return []
        if self.memory_thread_id is None:
            history = build_history()
        else:
            history = build_history(thread_id=self.memory_thread_id)
        return [dict(message) for message in history]

    def build_memory_context(self, intent: IntentType) -> str:
        if self.memory_manager is None or intent not in ORDINARY_MEMORY_INTENTS:
            return ""
        build_context = getattr(self.memory_manager, "build_routing_context", None)
        if build_context is None:
            return ""
        if self.memory_thread_id is None:
            context = str(build_context(intent)).strip()
        else:
            context = str(
                build_context(intent, thread_id=self.memory_thread_id)
            ).strip()
        heading = "## Memory Context"
        if context == heading:
            return ""
        if context.startswith(f"{heading}\n"):
            context = context[len(heading) :].lstrip()
        return context[:MEMORY_ROUTING_CONTEXT_MAX_CHARS].rstrip()

    def build_memory_debug_summary(
        self,
        intent: IntentType,
        current_user_text: str = "",
    ) -> str:
        del current_user_text
        if intent not in ORDINARY_MEMORY_INTENTS:
            return ""
        context = self.build_memory_context(intent)
        lines: list[str] = []
        if context:
            lines.append(f"Memory routing context: {len(context)} chars")
        manager = self.memory_manager
        fetch_diagnostic = getattr(manager, "latest_diagnostic", None)
        diagnostic = fetch_diagnostic() if callable(fetch_diagnostic) else None
        if isinstance(diagnostic, LLMCallDiagnostic):
            purpose = getattr(diagnostic.purpose, "name", str(diagnostic.purpose)).lower()
            reasoning = getattr(
                diagnostic.reasoning,
                "name",
                str(diagnostic.reasoning),
            ).lower()
            stream = "on" if diagnostic.stream else "off"
            detail = (
                f"Memory LLM diagnostic: purpose={purpose}, stream={stream}, "
                f"reasoning={reasoning}, status={diagnostic.status}, "
                f"timeout={diagnostic.timeout_seconds}s"
            )
            if diagnostic.error:
                detail += f", error={diagnostic.error}"
            lines.append(detail)
        return "\n".join(lines)

    def clear_request_cache(self) -> None:
        self.source_read_cache.clear()

    def clear_cache(self) -> None:
        self.clear_request_cache()
        self.patch_source_hint = None
        self.proposed_patch_draft = None
        self.revised_patch_draft = None
        self.code_explain_allow_symbol_search = True
