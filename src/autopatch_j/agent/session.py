"""请求共享业务依赖、焦点、取证缓存和未提交的草案。"""

from __future__ import annotations

import dataclasses
import pathlib
from typing import TYPE_CHECKING

from autopatch_j.core.domain import audit
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.project import repo_path

if TYPE_CHECKING:
    from autopatch_j.core.patching import search_replace
    from autopatch_j.core.patching import types
    from autopatch_j.core.patching import verification
    from autopatch_j.core.project import source_reader
    from autopatch_j.core.project import symbol_index
    from autopatch_j.core.review import artifacts
    from autopatch_j.core.review import workspace
    from autopatch_j.tools import contract


@dataclasses.dataclass
class AgentSession:
    """Agent 与 Tool 共享的运行上下文。

    这里保存的是一次 CLI 会话内需要共享的执行依赖和短期状态。长期状态
    由专门的 core service 负责，例如 workspace 由 ReviewWorkspaceManager 管理，
    普通历史和项目笔记分别使用官方 conversation 与 memory store。
    """

    repo_root: pathlib.Path
    artifact_manager: artifacts.ProjectArtifactStore
    workspace_manager: workspace.ReviewWorkspaceManager
    symbol_indexer: symbol_index.SymbolIndex
    patch_engine: search_replace.SearchReplacePatchEngine
    code_fetcher: source_reader.SourceReader
    patch_verifier: verification.PatchQualityVerifier | None = None

    current_finding: audit.FindingTask | None = None
    current_review_item: domain_workspace.ReviewPatchItem | None = None
    focus_paths: list[str] = dataclasses.field(default_factory=list)
    source_read_cache: dict[
        tuple[str, str, int | None], contract.ToolExecutionResult
    ] = dataclasses.field(default_factory=dict)
    patch_source_hint: str | None = None
    proposed_patch_draft: types.SearchReplacePatchDraft | None = None
    revised_patch_draft: types.SearchReplacePatchDraft | None = None
    notices: list[str] = dataclasses.field(default_factory=list)
    observations: list[contract.ToolExecutionResult] = dataclasses.field(
        default_factory=list
    )
    code_explain_allow_symbol_search: bool = True
    runtime_patch_constraints: dict[str, list[str]] = dataclasses.field(
        default_factory=dict
    )

    def set_focus_paths(self, paths: list[str] | None) -> None:
        """只保存安全的仓库内源码焦点，过滤状态目录与越界目标。"""
        normalized: list[str] = []
        for path in paths or []:
            try:
                clean = repo_path.to_repo_relative_path(
                    self.repo_root,
                    repo_path.resolve_repo_path(self.repo_root, path),
                )
            except repo_path.UnsafeRepoPathError:
                continue
            if (
                clean
                and not repo_path.is_project_state_path(clean)
                and clean not in normalized
            ):
                normalized.append(clean)
        self.focus_paths = normalized

    def is_focus_locked(self) -> bool:
        """当前请求是否限定为明确的文件集合。"""
        return bool(self.focus_paths)

    def is_path_in_focus(self, path: str) -> bool:
        """核对真实源码路径是否安全且属于本次焦点。"""
        try:
            safe_path = repo_path.to_repo_relative_path(
                self.repo_root,
                repo_path.resolve_repo_path(self.repo_root, path),
            )
        except repo_path.UnsafeRepoPathError:
            return False
        if repo_path.is_project_state_path(safe_path):
            return False
        if not self.focus_paths:
            return True
        return safe_path in self.focus_paths

    def normalize_repo_path(self, path: str) -> str:
        """统一请求内路径表示；实际准入仍须检查真实目标。"""
        return repo_path.normalize_repo_path(path)

    def fetch_cached_source_read(
        self, tool_name: str, path: str, line: int | None
    ) -> contract.ToolExecutionResult | None:
        """按工具、路径和行号读取本次请求缓存，不跨请求复用。"""
        key = (tool_name, self.normalize_repo_path(path), line)
        return self.source_read_cache.get(key)

    def persist_cached_source_read(
        self,
        tool_name: str,
        path: str,
        line: int | None,
        result: contract.ToolExecutionResult,
    ) -> None:
        """按工具语义保存本次源码观察，不作为可写源码证明。"""
        key = (tool_name, self.normalize_repo_path(path), line)
        self.source_read_cache[key] = result

    def set_proposed_patch_draft(
        self, draft: types.SearchReplacePatchDraft
    ) -> None:
        """暂存新草案，不改变审核队列或磁盘源码。"""
        self.proposed_patch_draft = draft

    def clear_proposed_patch_draft(self) -> None:
        """撤销本次尚未入队的新草案。"""
        self.proposed_patch_draft = None

    def pop_proposed_patch_draft(self) -> types.SearchReplacePatchDraft | None:
        """取出并清除暂存新草案，向 workflow 移交提交责任。"""
        draft = self.proposed_patch_draft
        self.proposed_patch_draft = None
        return draft

    def set_revised_patch_draft(
        self, draft: types.SearchReplacePatchDraft
    ) -> None:
        """暂存当前审核项的修订，不替换现有队列。"""
        self.revised_patch_draft = draft

    def pop_revised_patch_draft(self) -> types.SearchReplacePatchDraft | None:
        """取出并清除修订草案，等待审核项绑定校验。"""
        draft = self.revised_patch_draft
        self.revised_patch_draft = None
        return draft

    def record_runtime_patch_constraint(
        self, review_item_id: str, user_text: str
    ) -> None:
        """保存绑定当前审核项的用户原文要求，不提炼为长期偏好。"""
        text = user_text.strip()
        if not text:
            return
        constraints = self.runtime_patch_constraints.setdefault(
            review_item_id, []
        )
        if text not in constraints:
            constraints.append(text)

    def clear_runtime_patch_constraints(self, review_item_id: str) -> None:
        """清理指定审核项的临时要求，不影响同文件其他审核项。"""
        self.runtime_patch_constraints.pop(review_item_id, None)

    def build_runtime_patch_constraint_context(
        self, review_item_id: str
    ) -> str:
        """将当前审核项的临时原文要求重新投影到本轮请求。"""
        if not self.runtime_patch_constraints:
            return ""
        constraints = self.runtime_patch_constraints.get(review_item_id, ())
        if not constraints:
            return ""
        lines = [
            "## 当前补丁临时约束",
            "以下约束只绑定当前 review，不是项目长期规则：",
        ]
        lines.extend(f"- {item}" for item in constraints)
        return "\n".join(lines)

    def clear_request_cache(self) -> None:
        """清除本次取证缓存，下一请求须重新读取。"""
        self.source_read_cache.clear()

    def clear_cache(self) -> None:
        """清除临时草案、局部约束和取证缓存，保留持久会话与笔记。"""
        self.clear_request_cache()
        self.patch_source_hint = None
        self.proposed_patch_draft = None
        self.revised_patch_draft = None
        self.code_explain_allow_symbol_search = True
        self.runtime_patch_constraints.clear()
