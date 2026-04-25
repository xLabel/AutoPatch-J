from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopatch_j.core.artifact_manager import ArtifactManager
    from autopatch_j.core.code_fetcher import CodeFetcher
    from autopatch_j.core.index_service import IndexService
    from autopatch_j.core.patch_engine import PatchEngine
    from autopatch_j.core.patch_verifier import PatchVerifier
    from autopatch_j.tools.base import ToolResult

@dataclass
class AgentSession:
    """
    Agent 会话上下文：存放核心服务引用与当前状态边界 (Domain Model)
    职责：分离状态与执行逻辑，彻底斩断工具层与执行层的循环依赖。
    """
    repo_root: Path
    artifacts: ArtifactManager
    indexer: IndexService
    patch_engine: PatchEngine
    fetcher: CodeFetcher
    patch_verifier: PatchVerifier | None = None
    
    focus_paths: list[str] = field(default_factory=list)
    source_read_cache: dict[tuple[str, str | None, int | None], ToolResult] = field(default_factory=dict)
    patch_source_hint: str | None = None
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

    def record_action(self, action_fingerprint: str) -> None:
        self.action_history.append(action_fingerprint)
        if len(self.action_history) > 10:
            self.action_history.pop(0)

    def is_stuck_in_loop(self) -> bool:
        if len(self.action_history) >= 3:
            return self.action_history[-1] == self.action_history[-2] == self.action_history[-3]
        return False

    def clear_cache(self) -> None:
        self.source_read_cache.clear()
        self.patch_source_hint = None
        self.code_explain_allow_symbol_search = True
        self.action_history.clear()
