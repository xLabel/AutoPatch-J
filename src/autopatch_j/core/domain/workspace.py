from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autopatch_j.core.domain.scope import CodeScope
from autopatch_j.core.patching.types import SearchReplacePatchDraft, SyntaxCheckResult


class WorkspaceStatus(str, Enum):
    """工作台是否处于人工审核补丁的状态。"""

    IDLE = "idle"
    REVIEWING = "reviewing"


class PatchReviewStatus(str, Enum):
    """人工补丁审核项的生命周期状态。"""

    PENDING = "pending"
    APPLIED = "applied"
    DISCARDED = "discarded"


@dataclass(slots=True)
class PatchDraftSnapshot:
    """
    SearchReplacePatchDraft 的可持久化快照。

    用于写入 workspace JSON，避免把运行时对象和验证结果对象直接序列化。
    """

    file_path: str
    old_string: str
    new_string: str
    diff: str
    validation_status: str
    validation_message: str
    validation_errors: list[str] = field(default_factory=list)
    rationale: str | None = None
    source_hint: str | None = None
    target_check_id: str | None = None
    target_snippet: str | None = None

    @classmethod
    def from_patch_draft(cls, draft: SearchReplacePatchDraft) -> PatchDraftSnapshot:
        return cls(
            file_path=draft.file_path,
            old_string=draft.old_string,
            new_string=draft.new_string,
            diff=draft.diff,
            validation_status=draft.validation.status,
            validation_message=draft.validation.message,
            validation_errors=list(draft.validation.errors),
            rationale=draft.rationale,
            source_hint=draft.source_hint,
            target_check_id=draft.target_check_id,
            target_snippet=draft.target_snippet,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "old_string": self.old_string,
            "new_string": self.new_string,
            "diff": self.diff,
            "validation_status": self.validation_status,
            "validation_message": self.validation_message,
            "validation_errors": list(self.validation_errors),
            "rationale": self.rationale,
            "source_hint": self.source_hint,
            "target_check_id": self.target_check_id,
            "target_snippet": self.target_snippet,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PatchDraftSnapshot:
        return cls(
            file_path=str(data["file_path"]),
            old_string=str(data.get("old_string", "")),
            new_string=str(data.get("new_string", "")),
            diff=str(data.get("diff", "")),
            validation_status=str(data.get("validation_status", "unknown")),
            validation_message=str(data.get("validation_message", "")),
            validation_errors=[str(item) for item in data.get("validation_errors", [])],
            rationale=str(data["rationale"]) if data.get("rationale") is not None else None,
            source_hint=str(data["source_hint"]) if data.get("source_hint") is not None else None,
            target_check_id=str(data["target_check_id"]) if data.get("target_check_id") is not None else None,
            target_snippet=str(data["target_snippet"]) if data.get("target_snippet") is not None else None,
        )

    def to_patch_draft(self) -> SearchReplacePatchDraft:
        return SearchReplacePatchDraft(
            file_path=self.file_path,
            old_string=self.old_string,
            new_string=self.new_string,
            diff=self.diff,
            validation=SyntaxCheckResult(
                status=self.validation_status,
                message=self.validation_message,
                errors=list(self.validation_errors),
            ),
            status="ok" if self.validation_status in {"ok", "skipped", "unavailable"} else "invalid",
            message=self.validation_message,
            rationale=self.rationale,
            source_hint=self.source_hint,
            target_check_id=self.target_check_id,
            target_snippet=self.target_snippet,
        )


@dataclass(slots=True)
class ReviewPatchItem:
    """
    人工审核队列中的一个补丁项。

    它把补丁草案、目标文件、关联 finding 和审核状态绑定在一起，供 CLI 展示和 apply/discard 推进。
    """

    item_id: str
    file_path: str
    finding_ids: list[str]
    status: PatchReviewStatus
    draft: PatchDraftSnapshot

    def is_pending(self) -> bool:
        return self.status is PatchReviewStatus.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "file_path": self.file_path,
            "finding_ids": list(self.finding_ids),
            "status": self.status.value,
            "draft": self.draft.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewPatchItem:
        return cls(
            item_id=str(data["item_id"]),
            file_path=str(data["file_path"]),
            finding_ids=[str(item) for item in data.get("finding_ids", [])],
            status=PatchReviewStatus(str(data["status"])),
            draft=PatchDraftSnapshot.from_dict(dict(data["draft"])),
        )


@dataclass(slots=True)
class ReviewWorkspace:
    """
    待审补丁工作台领域模型。

    职责边界：
    1. 维护当前审核模式、扫描范围、补丁队列和游标位置。
    2. 定义 apply/discard/replace 后队列如何推进的领域规则。
    3. 不负责磁盘读写；持久化由 ReviewWorkspaceManager 和 ProjectArtifactStore 完成。
    """

    mode: WorkspaceStatus
    scope: CodeScope | None
    latest_scan_id: str | None
    patch_items: list[ReviewPatchItem]
    current_patch_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "scope": self.scope.to_dict() if self.scope else None,
            "latest_scan_id": self.latest_scan_id,
            "patch_items": [item.to_dict() for item in self.patch_items],
            "current_patch_index": self.current_patch_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReviewWorkspace:
        raw_scope = data.get("scope")
        scope = CodeScope.from_dict(dict(raw_scope)) if isinstance(raw_scope, dict) else None
        return cls(
            mode=WorkspaceStatus(str(data.get("mode", WorkspaceStatus.IDLE.value))),
            scope=scope,
            latest_scan_id=str(data["latest_scan_id"]) if data.get("latest_scan_id") is not None else None,
            patch_items=[ReviewPatchItem.from_dict(dict(item)) for item in data.get("patch_items", [])],
            current_patch_index=int(data.get("current_patch_index", 0)),
        )

    def current_patch(self) -> ReviewPatchItem | None:
        if not self.patch_items:
            return None
        if self.current_patch_index < 0 or self.current_patch_index >= len(self.patch_items):
            return None
        item = self.patch_items[self.current_patch_index]
        return item if item.is_pending() else None

    def review_progress(self) -> tuple[int, int]:
        total_count = len(self.patch_items)
        if self.current_patch() is None or total_count == 0:
            return 0, total_count
        return self.current_patch_index + 1, total_count

    def has_pending_patch(self) -> bool:
        return self.current_patch() is not None

    def mark_current_patch_applied(self) -> None:
        item = self.current_patch()
        if item:
            item.status = PatchReviewStatus.APPLIED
            self._advance_after_terminal_patch()

    def mark_current_patch_discarded(self) -> None:
        item = self.current_patch()
        if item:
            item.status = PatchReviewStatus.DISCARDED
            self._advance_after_terminal_patch()

    def replace_current_patch(self, replacement_item: ReviewPatchItem) -> None:
        if self.current_patch() is None:
            return
        self.patch_items[self.current_patch_index] = replacement_item
        self.mode = WorkspaceStatus.REVIEWING

    def _advance_after_terminal_patch(self) -> None:
        next_index: int | None = None
        for index in range(self.current_patch_index + 1, len(self.patch_items)):
            if self.patch_items[index].is_pending():
                next_index = index
                break

        if next_index is None:
            self.current_patch_index = len(self.patch_items)
            self.mode = WorkspaceStatus.IDLE
            return

        self.current_patch_index = next_index
        self.mode = WorkspaceStatus.REVIEWING
