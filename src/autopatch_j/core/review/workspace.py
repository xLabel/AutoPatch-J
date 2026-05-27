from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from autopatch_j.core.domain.scope import CodeScope
from autopatch_j.core.domain.workspace import (
    PatchDraftSnapshot,
    PatchReviewStatus,
    ReviewPatchItem,
    ReviewWorkspace,
    WorkspaceStatus,
)
from autopatch_j.core.patching.types import SearchReplacePatchDraft
from autopatch_j.core.review.artifacts import ProjectArtifactStore


@dataclass(slots=True)
class ReviewWorkspaceManager:
    """
    ReviewWorkspace 的持久化事务门面。

    职责边界：
    1. 从 ProjectArtifactStore 加载/保存 workspace，并在不存在时提供空闲工作台。
    2. 提供 edit() 事务和常用队列操作，如初始化审核队列、追加补丁、替换当前补丁。
    3. 不重新定义队列推进规则；apply/discard/replace 的领域行为仍在 ReviewWorkspace 中。
    """

    artifact_store: ProjectArtifactStore

    @contextmanager
    def edit(self) -> Iterator[ReviewWorkspace]:
        workspace = self.load()
        yield workspace
        self.save(workspace)

    def load(self) -> ReviewWorkspace:
        workspace = self.artifact_store.load_review_workspace()
        return workspace or self._build_idle_workspace()

    def save(self, workspace: ReviewWorkspace) -> None:
        self.artifact_store.save_review_workspace(workspace)

    def initialize_review(
        self,
        scope: CodeScope,
        latest_scan_id: str | None,
        patch_items: list[ReviewPatchItem],
    ) -> ReviewWorkspace:
        workspace = ReviewWorkspace(
            mode=WorkspaceStatus.REVIEWING if patch_items else WorkspaceStatus.IDLE,
            scope=scope,
            latest_scan_id=latest_scan_id,
            patch_items=list(patch_items),
            current_patch_index=0,
        )
        self.save(workspace)
        return workspace

    def clear(self) -> None:
        self.artifact_store.clear_review_workspace()

    def add_patch(self, draft: SearchReplacePatchDraft) -> None:
        with self.edit() as workspace:
            new_item_index = len(workspace.patch_items) + 1
            finding_ids = [draft.associated_finding_id] if draft.associated_finding_id else []
            review_item = ReviewPatchItem(
                item_id=f"item-{new_item_index}",
                file_path=draft.file_path,
                finding_ids=finding_ids,
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftSnapshot.from_patch_draft(draft),
            )
            workspace.patch_items.append(review_item)

            if workspace.mode is WorkspaceStatus.IDLE:
                workspace.mode = WorkspaceStatus.REVIEWING
                workspace.current_patch_index = 0

    def replace_current_patch(self, draft: SearchReplacePatchDraft) -> bool:
        with self.edit() as workspace:
            current_item = workspace.current_patch()
            if current_item is None:
                return False
            current_finding_ids = list(current_item.finding_ids)
            if (
                draft.associated_finding_id
                and current_finding_ids
                and draft.associated_finding_id not in current_finding_ids
            ):
                return False
            finding_ids = [draft.associated_finding_id] if draft.associated_finding_id else current_finding_ids
            if draft.associated_finding_id is None and current_finding_ids:
                draft.associated_finding_id = current_finding_ids[0]
            if draft.source_scan_id is None:
                draft.source_scan_id = current_item.draft.source_scan_id
            if draft.target_check_id is None:
                draft.target_check_id = current_item.draft.target_check_id
            if draft.target_snippet is None:
                draft.target_snippet = current_item.draft.target_snippet
            replacement_item = ReviewPatchItem(
                item_id=current_item.item_id,
                file_path=draft.file_path,
                finding_ids=finding_ids,
                status=PatchReviewStatus.PENDING,
                draft=PatchDraftSnapshot.from_patch_draft(draft),
            )
            workspace.replace_current_patch(replacement_item)
            return True

    def load_current_patch_draft(self) -> SearchReplacePatchDraft | None:
        workspace = self.load()
        current_item = workspace.current_patch()
        if current_item is None or not current_item.is_pending():
            return None
        return current_item.draft.to_patch_draft()

    def _build_idle_workspace(self) -> ReviewWorkspace:
        return ReviewWorkspace(
            mode=WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
