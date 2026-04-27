from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.models import (
    ActiveWorkspace,
    CodeScope,
    PatchReviewItem,
    WorkspaceStatus,
)


@dataclass(slots=True)
class WorkspaceManager:
    """
    工作台持久化与事务管理器 (Workspace Repository)。
    核心职责：
    1. 负责 ActiveWorkspace 领域对象的 I/O 持久化。
    2. 提供 edit() 事务会话，确保在业务修改后状态能自动同步回磁盘。
    """

    artifact_manager: ArtifactManager

    @contextmanager
    def edit(self) -> Iterator[ActiveWorkspace]:
        """
        开启一个工作台编辑事务。
        在 with 块结束时自动触发 self.save_workspace()，确保状态修改的原子性。
        """
        workspace = self.load_workspace()
        try:
            yield workspace
        finally:
            self.save_workspace(workspace)

    def load_workspace(self) -> ActiveWorkspace:
        """加载当前工作台状态，若不存在则返回默认的空闲状态"""
        workspace = self.artifact_manager.load_workspace()
        return workspace or self._build_idle_workspace()

    def save_workspace(self, workspace: ActiveWorkspace) -> None:
        """将工作台状态强制落盘"""
        self.artifact_manager.save_workspace(workspace)

    def initialize_review_workspace(
        self,
        scope: CodeScope,
        latest_scan_id: str | None,
        patch_items: list[PatchReviewItem],
    ) -> ActiveWorkspace:
        """初始化一个新的审核队列并立刻持久化"""
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
        """清空工作台状态"""
        self.artifact_manager.clear_workspace()

    def _build_idle_workspace(self) -> ActiveWorkspace:
        """构建内存中的初始空闲状态"""
        return ActiveWorkspace(
            mode=WorkspaceStatus.IDLE,
            scope=None,
            latest_scan_id=None,
            patch_items=[],
            current_patch_index=0,
        )
