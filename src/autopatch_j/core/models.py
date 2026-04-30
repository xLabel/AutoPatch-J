from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from autopatch_j.core.patch_engine import PatchDraft


class IntentType(str, Enum):
    """自然语言输入可进入的业务意图集合，由 IntentDetector 产出。"""

    CODE_AUDIT = "code_audit"
    CODE_EXPLAIN = "code_explain"
    GENERAL_CHAT = "general_chat"
    PATCH_EXPLAIN = "patch_explain"
    PATCH_REVISE = "patch_revise"


class ConversationRoute(str, Enum):
    """pending review 场景下的会话路由结果，由 ConversationRouter 产出。"""

    NEW_TASK = "new_task"
    REVIEW_CONTINUE = "review_continue"
    COMMAND = "command"


class WorkspaceStatus(str, Enum):
    """工作台是否处于人工审核补丁的状态。"""

    IDLE = "idle"
    REVIEWING = "reviewing"


class CodeScopeKind(str, Enum):
    """代码范围的粗粒度类型，用于展示和流程分支。"""

    SINGLE_FILE = "single_file"
    MULTI_FILE = "multi_file"
    PROJECT = "project"


class PatchReviewStatus(str, Enum):
    """人工补丁审核项的生命周期状态。"""

    PENDING = "pending"
    APPLIED = "applied"
    DISCARDED = "discarded"


class AuditFindingStatus(str, Enum):
    """审计 finding 在自动修复流程中的推进状态。"""

    PENDING = "pending"
    PATCH_READY = "patch_ready"
    FAILED = "failed"


class AuditAttemptOutcome(str, Enum):
    """单次 finding 修复尝试的归因结果。"""

    PATCH_READY = "patch_ready"
    RETRYABLE_ERROR = "retryable_error"
    NO_PATCH = "no_patch"


@dataclass(slots=True)
class CodeScope:
    """
    一次用户请求解析出的代码范围。

    source_roots 保留用户选择的原始入口，focus_files 是展开后的文件级范围。
    is_locked 为 True 时，Agent 和 Tool 只能在 focus_files 内读取或修改代码。
    """

    kind: CodeScopeKind
    source_roots: list[str]
    focus_files: list[str]
    is_locked: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "source_roots": list(self.source_roots),
            "focus_files": list(self.focus_files),
            "is_locked": self.is_locked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CodeScope:
        return cls(
            kind=CodeScopeKind(str(data["kind"])),
            source_roots=[str(item) for item in data.get("source_roots", [])],
            focus_files=[str(item) for item in data.get("focus_files", [])],
            is_locked=bool(data.get("is_locked", False)),
        )


@dataclass(slots=True)
class PatchDraftData:
    """
    PatchDraft 的可持久化快照。

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
    def fetch_from_patch_draft(cls, draft: PatchDraft) -> PatchDraftData:
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
    def from_dict(cls, data: dict[str, Any]) -> PatchDraftData:
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

    def fetch_patch_draft(self) -> PatchDraft:
        from autopatch_j.core.patch_verifier import SyntaxCheckResult
        return PatchDraft(
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
class PatchReviewItem:
    """
    人工审核队列中的一个补丁项。

    它把补丁草案、目标文件、关联 finding 和审核状态绑定在一起，供 CLI 展示和 apply/discard 推进。
    """

    item_id: str
    file_path: str
    finding_ids: list[str]
    status: PatchReviewStatus
    draft: PatchDraftData

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
    def from_dict(cls, data: dict[str, Any]) -> PatchReviewItem:
        return cls(
            item_id=str(data["item_id"]),
            file_path=str(data["file_path"]),
            finding_ids=[str(item) for item in data.get("finding_ids", [])],
            status=PatchReviewStatus(str(data["status"])),
            draft=PatchDraftData.from_dict(dict(data["draft"])),
        )


@dataclass(slots=True)
class AuditFindingItem:
    """
    扫描 finding 在本轮审计 workflow 中的待办项。

    它保存 F1/F2 这类逻辑句柄、重试次数和最后失败原因，不直接持久化到 workspace。
    """

    finding_id: str
    file_path: str
    check_id: str
    start_line: int
    end_line: int
    message: str
    snippet: str
    status: AuditFindingStatus = AuditFindingStatus.PENDING
    retry_count: int = 0
    last_error_code: str | None = None
    last_error_message: str | None = None

    def is_pending(self) -> bool:
        return self.status is AuditFindingStatus.PENDING


@dataclass(slots=True)
class AuditAttemptDecision:
    """
    一次 Agent 修复尝试后的流程决策。

    BacklogManager 根据工具消息推断该 finding 是否已产生补丁、是否可重试或应标记失败。
    """

    outcome: AuditAttemptOutcome
    error_code: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class ActiveWorkspace:
    """
    待审补丁工作台领域模型。

    职责边界：
    1. 维护当前审核模式、扫描范围、补丁队列和游标位置。
    2. 定义 apply/discard/replace 后队列如何推进的领域规则。
    3. 不负责磁盘读写；持久化由 WorkspaceManager 和 ArtifactManager 完成。
    """
    mode: WorkspaceStatus
    scope: CodeScope | None
    latest_scan_id: str | None
    patch_items: list[PatchReviewItem]
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
    def from_dict(cls, data: dict[str, Any]) -> ActiveWorkspace:
        raw_scope = data.get("scope")
        scope = CodeScope.from_dict(dict(raw_scope)) if isinstance(raw_scope, dict) else None
        return cls(
            mode=WorkspaceStatus(str(data.get("mode", WorkspaceStatus.IDLE.value))),
            scope=scope,
            latest_scan_id=str(data["latest_scan_id"]) if data.get("latest_scan_id") is not None else None,
            patch_items=[PatchReviewItem.from_dict(dict(item)) for item in data.get("patch_items", [])],
            current_patch_index=int(data.get("current_patch_index", 0)),
        )

    def get_current_patch(self) -> PatchReviewItem | None:
        if not self.patch_items:
            return None
        if self.current_patch_index < 0 or self.current_patch_index >= len(self.patch_items):
            return None
        return self.patch_items[self.current_patch_index]

    def get_review_progress(self) -> tuple[int, int]:
        total_count = len(self.patch_items)
        if self.get_current_patch() is None or total_count == 0:
            return 0, total_count
        return self.current_patch_index + 1, total_count

    def has_pending_patch(self) -> bool:
        return self.get_current_patch() is not None

    def mark_applied(self) -> None:
        item = self.get_current_patch()
        if item:
            item.status = PatchReviewStatus.APPLIED
            self._advance_after_terminal_patch()

    def mark_discarded(self) -> None:
        item = self.get_current_patch()
        if item:
            item.status = PatchReviewStatus.DISCARDED
            self._advance_after_terminal_patch()

    def replace_current_patch(self, replacement_item: PatchReviewItem) -> None:
        if self.get_current_patch() is None:
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
