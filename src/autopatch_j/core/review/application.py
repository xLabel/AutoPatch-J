"""人工审核决定、源码应用、同文件重定位与目标验证。"""

import copy
import dataclasses

from autopatch_j.core import finding
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.patching import search_replace
from autopatch_j.core.patching import types
from autopatch_j.core.patching import verification
from autopatch_j.core.review import workspace as review_workspace


@dataclasses.dataclass(frozen=True, slots=True)
class ReviewApplication:
    """文件写入事实与后续验证分别表达，验证失败不撤销写入事实。"""

    application: types.PatchApplicationResult
    verification: types.VerificationResult | None = None
    rebased: int = 0
    stale: int = 0
    queue_empty: bool = False
    workspace_error: str | None = None


class PatchReviewService:
    """协调人工选择后的业务状态；不输出终端文本。"""

    def __init__(
        self,
        workspace_manager: review_workspace.ReviewWorkspaceManager,
        patch_engine: search_replace.SearchReplacePatchEngine,
        verifier: verification.PatchQualityVerifier | None,
    ) -> None:
        self.workspace_manager = workspace_manager
        self.patch_engine = patch_engine
        self.verifier = verifier

    def apply(self, draft: types.SearchReplacePatchDraft) -> ReviewApplication:
        """应用当前草案，持久化阻断与最终状态后才复扫；写入事实独立返回。"""
        if draft.error_code == "STALE_DRAFT":
            return ReviewApplication(
                types.PatchApplicationResult(
                    False, error_code="STALE_DRAFT", message=draft.message
                )
            )
        workspace = self.workspace_manager.load()
        current = workspace.current_patch()
        if current is None or current.draft.to_patch_draft() != draft:
            return ReviewApplication(
                types.PatchApplicationResult(
                    False,
                    error_code="REVIEW_BINDING_CHANGED",
                    message="当前待审草案已变化，请重新查看后决定是否应用。",
                )
            )
        # 文件与工作台不能原子提交。先保存保守阻断状态，崩溃或末次保存
        # 失败时禁止重复应用；正常完成后以真实应用和重定位结果替换它。
        checkpoint = copy.deepcopy(workspace)
        for item in checkpoint.patch_items:
            if item.is_pending() and types.normalize_patch_path(
                item.file_path
            ) == types.normalize_patch_path(draft.file_path):
                item.draft.error_code = "STALE_DRAFT"
                item.draft.message = (
                    "应用结果尚未完成持久化，源码可能已经写入。"
                    "请核对源码，discard 或 abort 后重新扫描；不要重复应用。"
                )
        try:
            self.workspace_manager.save(checkpoint)
        except OSError as exc:
            return ReviewApplication(
                types.PatchApplicationResult(
                    False,
                    error_code="WORKSPACE_SAVE_FAILED",
                    message=f"无法保存审核阻断状态，源码未写入：{exc}",
                )
            )
        result = self.patch_engine.apply_patch(draft)
        if not result.applied:
            try:
                self.workspace_manager.save(workspace)
            except OSError as exc:
                return ReviewApplication(
                    result,
                    workspace_error=f"审核状态未恢复：{exc}；请重新扫描。",
                )
            return ReviewApplication(result)
        assert (
            result.source_region is not None
            and result.changed_region is not None
        )
        workspace.mark_current_patch_applied()
        rebased, stale = self._rebase_pending_same_file_patches(
            workspace=workspace,
            file_path=draft.file_path,
            source_region=result.source_region,
            changed_region=result.changed_region,
        )
        try:
            self.workspace_manager.save(workspace)
        except OSError as exc:
            return ReviewApplication(
                result,
                workspace_error=(
                    f"源码已写入，但审核状态未保存：{exc}。"
                    "已保留阻断草案；请核对源码，discard 或 abort 后重新扫描。"
                ),
            )
        queue_empty = not workspace.has_pending_patch()
        # 先保存已应用状态，复扫失败不能让同一草案仍处于待审状态。
        try:
            verified = (
                self.verifier.verify_finding_resolved(draft, result)
                if self.verifier
                else None
            )
        except (OSError, RuntimeError) as exc:
            verified = types.VerificationResult(
                types.VerificationOutcome.UNVERIFIED,
                f"已应用；后续验证失败：{exc}",
            )
        return ReviewApplication(result, verified, rebased, stale, queue_empty)

    def discard(self) -> bool:
        """丢弃当前草案并推进队列，返回是否已无待审项。"""
        with self.workspace_manager.edit() as workspace:
            workspace.mark_current_patch_discarded()
            return not workspace.has_pending_patch()

    def abort(self) -> None:
        """清空当前审核工作台。"""
        self.workspace_manager.clear()

    def _rebase_pending_same_file_patches(
        self,
        *,
        workspace: domain_workspace.ReviewWorkspace,
        file_path: str,
        source_region: finding.SourceRegion,
        changed_region: finding.SourceRegion,
    ) -> tuple[int, int]:
        """更新同文件非重叠待审区域；不可证明的匹配标记为失效。"""
        rebased_count = 0
        stale_count = 0
        normalized_file_path = types.normalize_patch_path(file_path)
        for item in workspace.patch_items:
            if item.status is not domain_workspace.PatchReviewStatus.PENDING:
                continue
            if (
                types.normalize_patch_path(item.file_path)
                != normalized_file_path
            ):
                continue
            if item.draft.error_code == "STALE_DRAFT":
                continue
            try:
                result = self.patch_engine.rebase_draft(
                    item.draft.to_patch_draft(),
                    source_region,
                    changed_region,
                )
                if not result.rebased:
                    item.draft.error_code = result.error_code
                    item.draft.message = result.message
                    stale_count += 1
                    continue
                assert result.build_result is not None
                validation = self._verify_rebased_syntax(
                    item.file_path,
                    result.build_result.updated_source,
                )
                message = (
                    result.message
                    if validation.status in {"ok", "skipped"}
                    else validation.message
                )
                item.draft = domain_workspace.PatchDraftSnapshot(
                    file_path=item.draft.file_path,
                    old_string=item.draft.old_string,
                    new_string=item.draft.new_string,
                    diff=result.build_result.diff,
                    match_region=result.build_result.match_region,
                    message=message,
                    validation_status=validation.status,
                    validation_message=validation.message,
                    validation_errors=list(validation.errors),
                    rationale=item.draft.rationale,
                    source_hint=item.draft.source_hint,
                    associated_finding_id=item.draft.associated_finding_id,
                    source_scan_id=item.draft.source_scan_id,
                    target_finding=result.rebased_target_finding,
                    error_code=None,
                )
                rebased_count += 1
            except (OSError, RuntimeError, ValueError) as exc:
                item.draft.error_code = "STALE_DRAFT"
                item.draft.message = (
                    "待审补丁已失效：重定位发生异常："
                    f"{exc}。请 discard，或 abort 后重新扫描。"
                )
                stale_count += 1
        return rebased_count, stale_count

    def _verify_rebased_syntax(
        self, file_path: str, updated_source: str
    ) -> types.SyntaxCheckResult:
        verifier = self.verifier
        if verifier is None:
            return types.SyntaxCheckResult(
                status="unavailable", message="未配置补丁语法校验器。"
            )
        return verifier.verify_syntax(file_path, updated_source)
