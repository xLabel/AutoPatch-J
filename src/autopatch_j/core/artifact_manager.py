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
from autopatch_j.validators.java_syntax import SyntaxValidationResult


@dataclass(slots=True)
class ArtifactManager:
    """
    状态持久化管家 (Core Service)
    职责：统一管理 .autopatch-j 下的扫描快照、工作台快照与兼容层补丁存储。
    """

    repo_root: Path
    state_dir: Path = field(init=False)
    findings_dir: Path = field(init=False)
    patches_dir: Path = field(init=False)
    workspace_file: Path = field(init=False)

    def __post_init__(self) -> None:
        self.state_dir = get_project_state_dir(self.repo_root)
        self.findings_dir = self.state_dir / "findings"
        self.patches_dir = self.state_dir / "patches"
        self.workspace_file = self.state_dir / "workspace.json"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        self.patches_dir.mkdir(parents=True, exist_ok=True)

    def persist_scan_result(self, result: ScanResult) -> str:
        artifact_id = self._generate_id("scan")
        target_path = self.findings_dir / f"{artifact_id}.json"
        target_path.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return artifact_id

    def fetch_scan_result(self, artifact_id: str) -> ScanResult | None:
        target_path = self.findings_dir / f"{artifact_id}.json"
        if not target_path.exists():
            return None
        try:
            data = json.loads(target_path.read_text(encoding="utf-8"))
            return ScanResult.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def fetch_finding_by_index(self, artifact_id: str, index: int) -> Finding | None:
        result = self.fetch_scan_result(artifact_id)
        if result is None or index < 0 or index >= len(result.findings):
            return None
        return result.findings[index]

    def persist_workspace(self, workspace: ActiveWorkspace) -> None:
        self.workspace_file.write_text(
            json.dumps(workspace.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def fetch_workspace(self) -> ActiveWorkspace | None:
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

    def persist_pending_patch(self, draft: PatchDraft) -> None:
        workspace = self.fetch_workspace()
        if workspace is None:
            pending_drafts = [draft] + self._fetch_legacy_pending_patches()
            self._persist_workspace_pending_drafts([], pending_drafts, None)
            self._clear_legacy_pending_files()
            return

        head_items = list(workspace.patch_items[: workspace.current_patch_index])
        pending_drafts = [draft] + [
            self._build_patch_draft_from_review_item(item)
            for item in workspace.fetch_remaining_patch_items()
        ]
        self._persist_workspace_pending_drafts(head_items, pending_drafts, workspace)
        self._clear_legacy_pending_files()

    def fetch_pending_patches(self) -> list[PatchDraft]:
        workspace = self.fetch_workspace()
        if workspace is not None and workspace.verify_has_pending_patch():
            return [
                self._build_patch_draft_from_review_item(item)
                for item in workspace.fetch_remaining_patch_items()
            ]
        return self._fetch_legacy_pending_patches()

    def fetch_pending_patch(self) -> PatchDraft | None:
        queue = self.fetch_pending_patches()
        return queue[0] if queue else None

    def pop_pending_patch(self) -> None:
        workspace = self.fetch_workspace()
        if workspace is None:
            pending_drafts = self._fetch_legacy_pending_patches()
            if pending_drafts:
                pending_drafts.pop(0)
            self._persist_workspace_pending_drafts([], pending_drafts, None)
            self._clear_legacy_pending_files()
            return

        head_items = list(workspace.patch_items[: workspace.current_patch_index])
        pending_drafts = [
            self._build_patch_draft_from_review_item(item)
            for item in workspace.fetch_remaining_patch_items()
        ]
        if pending_drafts:
            pending_drafts.pop(0)
        self._persist_workspace_pending_drafts(head_items, pending_drafts, workspace)
        self._clear_legacy_pending_files()

    def discard_followup_patches(self) -> list[PatchDraft]:
        pending_drafts = self.fetch_pending_patches()
        if len(pending_drafts) <= 1:
            return []

        workspace = self.fetch_workspace()
        if workspace is None:
            self._persist_workspace_pending_drafts([], pending_drafts[:1], None)
            self._clear_legacy_pending_files()
            return list(pending_drafts[1:])

        head_items = list(workspace.patch_items[: workspace.current_patch_index])
        self._persist_workspace_pending_drafts(head_items, pending_drafts[:1], workspace)
        self._clear_legacy_pending_files()
        return list(pending_drafts[1:])

    def clear_pending_patch(self) -> None:
        self.clear_workspace()
        self._clear_legacy_pending_files()

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
        self.persist_workspace(workspace)

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

    def _fetch_legacy_pending_patches(self) -> list[PatchDraft]:
        target_path = self.patches_dir / "pending_queue.json"
        if not target_path.exists():
            return []
        try:
            queue_data = json.loads(target_path.read_text(encoding="utf-8"))
            drafts: list[PatchDraft] = []
            for data in queue_data:
                validation = SyntaxValidationResult(
                    status=str(data.get("validation", {}).get("status", "unknown")),
                    message=str(data.get("validation", {}).get("message", "")),
                    errors=[str(item) for item in data.get("validation", {}).get("errors", [])],
                )
                drafts.append(
                    PatchDraft(
                        file_path=str(data["file_path"]),
                        old_string=str(data["old_string"]),
                        new_string=str(data["new_string"]),
                        diff=str(data["diff"]),
                        validation=validation,
                        status=str(data.get("status", "unknown")),
                        message=str(data.get("message", "")),
                        rationale=str(data["rationale"]) if data.get("rationale") is not None else None,
                        target_check_id=(
                            str(data["target_check_id"]) if data.get("target_check_id") is not None else None
                        ),
                        target_snippet=(
                            str(data["target_snippet"]) if data.get("target_snippet") is not None else None
                        ),
                    )
                )
            return drafts
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return []

    def _clear_legacy_pending_files(self) -> None:
        for legacy_path in [
            self.patches_dir / "pending_queue.json",
            self.patches_dir / "current_pending.json",
        ]:
            if legacy_path.exists():
                legacy_path.unlink()

    def _generate_id(self, prefix: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return f"{prefix}-{timestamp}"
