"""人工审核工作台的加载、事务编辑及绑定安全的草案替换。"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
import dataclasses

from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.patching import types
from autopatch_j.core.review import artifacts


@dataclasses.dataclass(slots=True)
class ReviewWorkspaceManager:
    """ReviewWorkspace 的持久化事务门面。

    职责边界：
    1. 从 ProjectArtifactStore 加载/保存 workspace，并在不存在时提供空闲工作台。
    2. 提供 edit() 事务和常用队列操作，如初始化审核队列、追加补丁、替换当前补丁。
    3. 不重新定义队列推进规则；apply/discard/replace 的领域行为仍在 ReviewWorkspace 中。
    """

    artifact_store: artifacts.ProjectArtifactStore

    @contextlib.contextmanager
    def edit(self) -> Generator[domain_workspace.ReviewWorkspace]:
        """加载可修改工作台，仅在调用块正常结束后保存。"""
        workspace = self.load()
        yield workspace
        self.save(workspace)

    def load(self) -> domain_workspace.ReviewWorkspace:
        """读取持久工作台，未保存时返回空闲状态。"""
        workspace = self.artifact_store.load_review_workspace()
        return workspace or self._build_idle_workspace()

    def save(self, workspace: domain_workspace.ReviewWorkspace) -> None:
        """保存调用方提交的审核领域状态。"""
        self.artifact_store.save_review_workspace(workspace)

    def initialize_review(
        self,
        scope: domain_scope.CodeScope,
        latest_scan_id: str | None,
        patch_items: list[domain_workspace.ReviewPatchItem],
    ) -> domain_workspace.ReviewWorkspace:
        """用当前扫描和范围建立新的审核队列。"""
        workspace = domain_workspace.ReviewWorkspace(
            mode=(
                domain_workspace.WorkspaceStatus.REVIEWING
                if patch_items
                else domain_workspace.WorkspaceStatus.IDLE
            ),
            scope=scope,
            latest_scan_id=latest_scan_id,
            patch_items=list(patch_items),
            current_patch_index=0,
        )
        self.save(workspace)
        return workspace

    def clear(self) -> None:
        """删除审核快照，保留普通会话、笔记和其他项目数据。"""
        self.artifact_store.clear_review_workspace()

    def add_patch(self, draft: types.SearchReplacePatchDraft) -> None:
        """将暂存草案追加为待审项，必要时开启审核状态。"""
        with self.edit() as workspace:
            new_item_index = len(workspace.patch_items) + 1
            finding_ids = (
                [draft.associated_finding_id]
                if draft.associated_finding_id
                else []
            )
            review_item = domain_workspace.ReviewPatchItem(
                item_id=f"item-{new_item_index}",
                file_path=draft.file_path,
                finding_ids=finding_ids,
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot.from_patch_draft(
                    draft
                ),
            )
            workspace.patch_items.append(review_item)

            if workspace.mode is domain_workspace.WorkspaceStatus.IDLE:
                workspace.mode = domain_workspace.WorkspaceStatus.REVIEWING
                workspace.current_patch_index = 0

    def replace_current_patch(
        self, draft: types.SearchReplacePatchDraft
    ) -> bool:
        """只替换文件、scan 和 finding 绑定一致的当前待审项。"""
        with self.edit() as workspace:
            current_item = workspace.current_patch()
            if current_item is None:
                return False
            current_draft = current_item.draft.to_patch_draft()
            if current_draft.error_code == "STALE_DRAFT":
                return False
            if types.normalize_patch_path(
                draft.file_path
            ) != types.normalize_patch_path(current_draft.file_path):
                return False
            if draft.error_code is not None:
                return False
            if (
                draft.associated_finding_id
                != current_draft.associated_finding_id
            ):
                return False
            if draft.source_scan_id != current_draft.source_scan_id:
                return False
            if draft.target_finding != current_draft.target_finding:
                return False
            if (
                draft.target_finding is not None
                and not draft.match_region.intersects(
                    draft.target_finding.region
                )
            ):
                return False
            expected_finding_ids = (
                [draft.associated_finding_id]
                if draft.associated_finding_id
                else []
            )
            if current_item.finding_ids != expected_finding_ids:
                return False
            replacement_item = domain_workspace.ReviewPatchItem(
                item_id=current_item.item_id,
                file_path=draft.file_path,
                finding_ids=list(current_item.finding_ids),
                status=domain_workspace.PatchReviewStatus.PENDING,
                draft=domain_workspace.PatchDraftSnapshot.from_patch_draft(
                    draft
                ),
            )
            workspace.replace_current_patch(replacement_item)
            return True

    def load_current_patch_draft(self) -> types.SearchReplacePatchDraft | None:
        """返回当前待审草案；空队列或非待审状态返回 None。"""
        workspace = self.load()
        current_item = workspace.current_patch()
        if current_item is None or not current_item.is_pending():
            return None
        return current_item.draft.to_patch_draft()

    def _build_idle_workspace(self) -> domain_workspace.ReviewWorkspace:
        return domain_workspace.ReviewWorkspace(
            mode=domain_workspace.WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
