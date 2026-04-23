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

    def fetch_current_patch_item(self) -> PatchReviewItem | None:
        return self.fetch_workspace().fetch_current_patch_item()

    def fetch_remaining_patch_items(self) -> list[PatchReviewItem]:
        return self.fetch_workspace().fetch_remaining_patch_items()

    def fetch_review_progress(self) -> tuple[int, int]:
        return self.fetch_workspace().fetch_review_progress()

    def verify_has_pending_patch(self) -> bool:
        return self.fetch_workspace().verify_has_pending_patch()

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
        current_item = workspace.fetch_current_patch_item()
        if current_item is None:
            return workspace
        current_item.status = PatchReviewStatus.APPLIED
        self._advance_after_terminal_patch(workspace)
        self.artifacts.persist_workspace(workspace)
        return workspace

    def persist_discarded_current_patch(self) -> ActiveWorkspace:
        workspace = self.fetch_workspace()
        current_item = workspace.fetch_current_patch_item()
        if current_item is None:
            return workspace
        current_item.status = PatchReviewStatus.DISCARDED
        self._advance_after_terminal_patch(workspace)
        self.artifacts.persist_workspace(workspace)
        return workspace

    def replace_remaining_patch_items(
        self,
        replacement_items: list[PatchReviewItem],
    ) -> ActiveWorkspace:
        workspace = self.fetch_workspace()
        head_items = list(workspace.patch_items[: workspace.current_patch_index])
        workspace.patch_items = head_items + list(replacement_items)
        workspace.current_patch_index = len(head_items)
        if replacement_items:
            workspace.mode = WorkspaceStatus.REVIEWING
        else:
            workspace.mode = WorkspaceStatus.IDLE
        self.artifacts.persist_workspace(workspace)
        return workspace

    def clear_workspace(self) -> None:
        self.artifacts.clear_workspace()

    def _advance_after_terminal_patch(self, workspace: ActiveWorkspace) -> None:
        next_index: int | None = None
        for index in range(workspace.current_patch_index + 1, len(workspace.patch_items)):
            if workspace.patch_items[index].verify_pending():
                next_index = index
                break

        if next_index is None:
            workspace.current_patch_index = len(workspace.patch_items)
            workspace.mode = WorkspaceStatus.IDLE
            return

        workspace.current_patch_index = next_index
        workspace.mode = WorkspaceStatus.REVIEWING

    def _build_idle_workspace(self) -> ActiveWorkspace:
        return ActiveWorkspace(
            mode=WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
