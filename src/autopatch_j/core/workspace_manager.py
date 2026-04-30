from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)
from autopatch_j.core.patch_engine import PatchDraft


@dataclass(slots=True)
class WorkspaceManager:
    """
    工作台业务逻辑管理器 (Workspace Domain Manager)。
    职责：
    1. 负责 ActiveWorkspace 领域对象的业务操作（如添加补丁、加载当前补丁）。
    2. 提供 edit() 事务会话，确保业务修改的原子性。
    """

    artifact_manager: ArtifactManager

    @contextmanager
    def edit(self) -> Iterator[ActiveWorkspace]:
        """开启一个工作台编辑事务，结束时自动落盘。"""
        workspace = self.load_workspace()
        try:
            yield workspace
        finally:
            self.save_workspace(workspace)

    def load_workspace(self) -> ActiveWorkspace:
        """加载当前工作台状态，若不存在则返回默认空闲状态。"""
        workspace = self.artifact_manager.load_workspace()
        return workspace or self._build_idle_workspace()

    def save_workspace(self, workspace: ActiveWorkspace) -> None:
        """持久化工作台状态。"""
        self.artifact_manager.save_workspace(workspace)

    def initialize_review_workspace(
        self,
        scope: CodeScope,
        latest_scan_id: str | None,
        patch_items: list[PatchReviewItem],
    ) -> ActiveWorkspace:
        """初始化一个新的审核队列并立刻持久化。"""
        workspace = ActiveWorkspace(
            mode=WorkspaceStatus.REVIEWING if patch_items else WorkspaceStatus.IDLE,
            scope=scope,
            latest_scan_id=latest_scan_id,
            patch_items=list(patch_items),
            current_patch_index=0,
        )
        self.save_workspace(workspace)
        return workspace

    def clear_workspace(self) -> None:
        """清空工作台状态。"""
        self.artifact_manager.clear_workspace()

    def add_pending_patch(self, draft: PatchDraft) -> None:
        """将一个新的补丁草案加入审核队列。"""
        with self.edit() as workspace:
            new_item_index = len(workspace.patch_items) + 1
            finding_ids = [draft.target_check_id] if draft.target_check_id else []
            review_item = PatchReviewItem(
                item_id=f"item-{new_item_index}",
                file_path=draft.file_path,
                finding_ids=finding_ids,
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftData.fetch_from_patch_draft(draft),
            )
            workspace.patch_items.append(review_item)
            
            if workspace.mode is WorkspaceStatus.IDLE:
                workspace.mode = WorkspaceStatus.REVIEWING
                workspace.current_patch_index = 0

    def replace_current_patch(self, draft: PatchDraft) -> bool:
        """用新的草案替换当前正在审核的补丁，不改变后续队列。"""
        with self.edit() as workspace:
            current_item = workspace.get_current_patch()
            if current_item is None:
                return False
            finding_ids = [draft.target_check_id] if draft.target_check_id else list(current_item.finding_ids)
            replacement_item = PatchReviewItem(
                item_id=current_item.item_id,
                file_path=draft.file_path,
                finding_ids=finding_ids,
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftData.fetch_from_patch_draft(draft),
            )
            workspace.replace_current_patch(replacement_item)
            return True

    def load_pending_patch(self) -> PatchDraft | None:
        """加载当前正在等待审核的补丁草案。"""
        workspace = self.load_workspace()
        current_item = workspace.get_current_patch()
        if current_item is None or not current_item.is_pending():
            return None
        return current_item.draft.fetch_patch_draft()

    def _build_idle_workspace(self) -> ActiveWorkspace:
        """构建内存中的初始空闲状态。"""
        return ActiveWorkspace(
            mode=WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
