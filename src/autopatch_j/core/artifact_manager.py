from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from autopatch_j.config import get_project_state_dir
from autopatch_j.core.models import (
    ActiveWorkspace,
    PatchDraftData,
    PatchReviewItem,
    PatchReviewStatus,
    WorkspaceStatus,
)
from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.scanners.base import Finding, ScanResult


@dataclass(slots=True)
class ArtifactManager:
    """
    状态持久化管家 (Core Service)
    职责：统一管理 .autopatch-j 下的扫描快照、工作台快照与兼容层补丁存储。
    """

    repo_root: Path
    state_dir: Path = field(init=False)
    findings_dir: Path = field(init=False)
    workspace_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = get_project_state_dir(self.repo_root)
        self.findings_dir = self.state_dir / "findings"
        self.workspace_file = self.state_dir / "workspace.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.findings_dir.mkdir(parents=True, exist_ok=True)

    def save_scan_result(self, result: ScanResult) -> str:
        artifact_id = self._generate_id("scan")
        target_path = self.findings_dir / f"{artifact_id}.json"
        target_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return artifact_id

    def load_scan_result(self, artifact_id: str) -> ScanResult | None:
        target_path = self.findings_dir / f"{artifact_id}.json"
        if not target_path.exists():
            return None
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            return ScanResult.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def get_finding_by_index(self, artifact_id: str, index: int) -> Finding | None:
        result = self.load_scan_result(artifact_id)
        if result is None or index < 0 or index >= len(result.findings):
            return None
        return result.findings[index]

    def save_workspace(self, workspace: ActiveWorkspace) -> None:
        self.workspace_file.write_text(
            json.dumps(workspace.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_workspace(self) -> ActiveWorkspace | None:
        if not self.workspace_file.exists():
            return None
        try:
            data = json.loads(self.workspace_file.read_text(encoding="utf-8"))
            return ActiveWorkspace.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def clear_workspace(self) -> None:
        if self.workspace_file.exists():
            self.workspace_file.unlink()

    # --- Pending Patch compatibility layer ---

    def save_pending_patch(self, draft: PatchDraft) -> None:
        workspace = self.load_workspace()
        if workspace is None:
            self._persist_workspace_pending_drafts([], [draft], None)
            return

        head_items = list(workspace.patch_items[: workspace.current_patch_index])
        pending_drafts = [draft] + [
            self._build_patch_draft_from_review_item(item)
            for item in workspace.get_remaining_patches()
        ]
        self._persist_workspace_pending_drafts(head_items, pending_drafts, workspace)

    def load_pending_patches(self) -> list[PatchDraft]:
        workspace = self.load_workspace()
        if workspace is None or not workspace.has_pending_patch():
            return []
        return [
            self._build_patch_draft_from_review_item(item)
            for item in workspace.get_remaining_patches()
        ]

    def load_pending_patch(self) -> PatchDraft | None:
        queue = self.load_pending_patches()
        return queue[0] if queue else None

    def clear_pending_patch(self) -> None:
        self.clear_workspace()

    # --- helpers ---

    def _persist_workspace_pending_drafts(
        self,
        head_items: list[PatchReviewItem],
        pending_drafts: list[PatchDraft],
        base_workspace: ActiveWorkspace | None,
    ) -> None:
        pending_items = [
            self._build_patch_review_item_from_draft(
                draft=draft,
                item_index=len(head_items) + offset,
            )
            for offset, draft in enumerate(pending_drafts, start=1)
        ]
        all_items = list(head_items) + pending_items
        current_patch_index = len(head_items) if all_items else 0
        workspace = ActiveWorkspace(
            mode=WorkspaceStatus.REVIEWING if pending_items else WorkspaceStatus.IDLE,
            scope=base_workspace.scope if base_workspace else None,
            latest_scan_id=base_workspace.latest_scan_id if base_workspace else None,
            patch_items=all_items,
            current_patch_index=current_patch_index,
        )
        self.save_workspace(workspace)

    def _build_patch_review_item_from_draft(self, draft: PatchDraft, item_index: int) -> PatchReviewItem:
        finding_ids: list[str] = [draft.target_check_id] if draft.target_check_id else []
        return PatchReviewItem(
            item_id=f"item-{item_index}",
            file_path=draft.file_path,
            finding_ids=finding_ids,
            status=PatchReviewStatus.PENDING,
            draft=PatchDraftData.fetch_from_patch_draft(draft),
        )

    def _build_patch_draft_from_review_item(self, item: PatchReviewItem) -> PatchDraft:
        return item.draft.fetch_patch_draft()

    def _generate_id(self, prefix: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{prefix}-{timestamp}"
