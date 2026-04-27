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
class WorkflowService:
    """
    工作流编排服务 (Core Service)
    职责：统一驱动工作台状态推进，不在 CLI 或 Agent 中分散维护审核状态。
    """

    artifacts: ArtifactManager

    def fetch_workspace(self) -> ActiveWorkspace:
        workspace = self.artifacts.fetch_workspace()
        return workspace or self._build_idle_workspace()

    def get_current_patch(self) -> PatchReviewItem | None:
        return self.fetch_workspace().get_current_patch()

    def get_remaining_patches(self) -> list[PatchReviewItem]:
        return self.fetch_workspace().get_remaining_patches()

    def get_review_progress(self) -> tuple[int, int]:
        return self.fetch_workspace().get_review_progress()

    def has_pending_patch(self) -> bool:
        return self.fetch_workspace().has_pending_patch()

    def persist_review_workspace(
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
        self.artifacts.persist_workspace(workspace)
        return workspace

    def persist_idle_workspace(self) -> ActiveWorkspace:
        workspace = self._build_idle_workspace()
        self.artifacts.persist_workspace(workspace)
        return workspace

    def persist_applied_current_patch(self) -> ActiveWorkspace:
        workspace = self.fetch_workspace()
        workspace.mark_applied()
        self.artifacts.persist_workspace(workspace)
        return workspace

    def persist_discarded_current_patch(self) -> ActiveWorkspace:
        workspace = self.fetch_workspace()
        workspace.mark_discarded()
        self.artifacts.persist_workspace(workspace)
        return workspace

    def replace_remaining_patch_items(
        self,
        replacement_items: list[PatchReviewItem],
    ) -> ActiveWorkspace:
        workspace = self.fetch_workspace()
        workspace.replace_tail(replacement_items)
        self.artifacts.persist_workspace(workspace)
        return workspace

    def clear_workspace(self) -> None:
        self.artifacts.clear_workspace()

    def _build_idle_workspace(self) -> ActiveWorkspace:
        return ActiveWorkspace(
            mode=WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
