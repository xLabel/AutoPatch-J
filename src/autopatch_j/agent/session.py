from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory.errors import MemoryStorageError
from autopatch_j.llm.options import LLMCallDiagnostic
from autopatch_j.core.project import (
    UnsafeRepoPathError,
    is_project_state_path,
    normalize_repo_path,
    resolve_repo_path,
    to_repo_relative_path,
)

if TYPE_CHECKING:
    from autopatch_j.core.review import ProjectArtifactStore
    from autopatch_j.core.project import SourceReader
    from autopatch_j.core.memory import MemoryManager
    from autopatch_j.core.memory.models import MemoryRequestState
    from autopatch_j.core.patching import SearchReplacePatchDraft, SearchReplacePatchEngine
    from autopatch_j.core.patching import PatchQualityVerifier
    from autopatch_j.core.project import SymbolIndex
    from autopatch_j.core.review import ReviewWorkspaceManager
    from autopatch_j.tools.contract import ToolExecutionResult


ORDINARY_MEMORY_INTENTS = {IntentType.CODE_EXPLAIN, IntentType.GENERAL_CHAT}
_PATCH_CONSTRAINT_RE = re.compile(
    r"(?:不要|禁止|避免|必须|不能|别用|改成|改为|请用|"
    r"\bdo not\b|\bdon't\b|\bavoid\b|\bmust\b|\binstead\b)",
    re.IGNORECASE,
)
MAX_RUNTIME_PATCH_CONSTRAINT_CHARS = 1_000


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
    memory_request_state: MemoryRequestState | None = None

    focus_paths: list[str] = field(default_factory=list)
    source_read_cache: dict[tuple[str, str, int | None], ToolExecutionResult] = field(default_factory=dict)
    patch_source_hint: str | None = None
    proposed_patch_draft: SearchReplacePatchDraft | None = None
    revised_patch_draft: SearchReplacePatchDraft | None = None
    code_explain_allow_symbol_search: bool = True
    runtime_patch_constraints: dict[str, list[str]] = field(default_factory=dict)

    def set_focus_paths(self, paths: list[str] | None) -> None:
        normalized: list[str] = []
        for path in paths or []:
            try:
                clean = to_repo_relative_path(self.repo_root, resolve_repo_path(self.repo_root, path))
            except UnsafeRepoPathError:
                continue
            if clean and not is_project_state_path(clean) and clean not in normalized:
                normalized.append(clean)
        self.focus_paths = normalized

    def is_focus_locked(self) -> bool:
        return bool(self.focus_paths)

    def is_path_in_focus(self, path: str) -> bool:
        try:
            safe_path = to_repo_relative_path(self.repo_root, resolve_repo_path(self.repo_root, path))
        except UnsafeRepoPathError:
            return False
        if is_project_state_path(safe_path):
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

    def bind_memory_request(self, state: MemoryRequestState) -> None:
        self.memory_request_state = state

    def clear_memory_request(self) -> None:
        self.memory_request_state = None

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

    def record_runtime_patch_constraint(self, file_path: str, user_text: str) -> None:
        text = " ".join(user_text.split())[:MAX_RUNTIME_PATCH_CONSTRAINT_CHARS]
        if not text or not _PATCH_CONSTRAINT_RE.search(text):
            return
        path = self.normalize_repo_path(file_path)
        constraints = self.runtime_patch_constraints.setdefault(path, [])
        if text not in constraints:
            constraints.append(text)

    def build_runtime_patch_constraint_context(self, file_path: str) -> str:
        if not self.runtime_patch_constraints:
            return ""
        constraints = self.runtime_patch_constraints.get(
            self.normalize_repo_path(file_path),
            (),
        )
        if not constraints:
            return ""
        lines = [
            "## 当前补丁临时约束",
            "以下约束只绑定当前 review，不是项目长期规则：",
        ]
        lines.extend(f"- {item}" for item in constraints)
        return "\n".join(lines)

    def build_thread_history(
        self,
        intent: IntentType,
        *,
        max_tokens: int,
    ) -> list[dict[str, Any]]:
        if self.memory_manager is None or intent not in ORDINARY_MEMORY_INTENTS:
            return []
        build_history = getattr(self.memory_manager, "build_thread_history", None)
        if build_history is None:
            return []
        try:
            if self.memory_thread_id is None:
                history = build_history(max_tokens=max_tokens)
            else:
                history = build_history(
                    thread_id=self.memory_thread_id,
                    max_tokens=max_tokens,
                )
        except MemoryStorageError:
            return []
        return [dict(message) for message in history]

    def build_memory_debug_summary(
        self,
        intent: IntentType,
        current_user_text: str = "",
    ) -> str:
        del current_user_text
        if intent not in ORDINARY_MEMORY_INTENTS:
            return ""
        lines: list[str] = []
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
        self.runtime_patch_constraints.clear()
