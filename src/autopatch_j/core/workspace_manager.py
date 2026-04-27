from __future__ import annotations

from dataclasses import dataclass

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)


@dataclass(slots=True)
class WorkspaceManager:
    """
    工作台状态管理服务 (Workspace Orchestrator)。
    核心职责：统一驱动 ActiveWorkspace 的状态推进和持久化（写入磁盘）。
    确保在 CLI 或 Agent 中不分散维护审核状态，保证人工确认流的原子性和连续性。
    """

    artifact_manager: ArtifactManager

    def load_workspace(self) -> ActiveWorkspace:
        workspace = self.artifact_manager.load_workspace()
        return workspace or self._build_idle_workspace()

    def save_workspace(self, workspace: ActiveWorkspace) -> None:
        self.artifact_manager.save_workspace(workspace)

    def initialize_review_workspace(
        self,
        scope: CodeScope,
        latest_scan_id: str | None,
        patch_items: list[PatchReviewItem],
    ) -> ActiveWorkspace:
        workspace = ActiveWorkspace(
            mode=WorkspaceStatus.REVIEWING if patch_items else WorkspaceStatus.IDLE,
            scope=scope,
            latest_scan_id=latest_scan_id,
            patch_items=list(patch_items),
            current_patch_index=0,
        )
        self.artifact_manager.save_workspace(workspace)
        return workspace

    def persist_idle_workspace(self) -> ActiveWorkspace:
        workspace = self._build_idle_workspace()
        self.artifact_manager.save_workspace(workspace)
        return workspace

    def mark_current_patch_applied(self) -> ActiveWorkspace:
        workspace = self.load_workspace()
        workspace.mark_applied()
        self.artifact_manager.save_workspace(workspace)
        return workspace

    def mark_current_patch_discarded(self) -> ActiveWorkspace:
        workspace = self.load_workspace()
        workspace.mark_discarded()
        self.artifact_manager.save_workspace(workspace)
        return workspace

    def replace_remaining_patch_items(
        self,
        replacement_items: list[PatchReviewItem],
    ) -> ActiveWorkspace:
        workspace = self.load_workspace()
        workspace.replace_tail(replacement_items)
        self.artifact_manager.save_workspace(workspace)
        return workspace

    def clear_workspace(self) -> None:
        self.artifact_manager.clear_workspace()

    def _build_idle_workspace(self) -> ActiveWorkspace:
        return ActiveWorkspace(
            mode=WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
