from __future__ import annotations

from dataclasses import dataclass

from autopatch_j.core.finding import FindingIdentity
from autopatch_j.core.review.finding_lookup import (
    FindingLookupError,
    FindingLookupResult,
    parse_finding_handle,
    resolve_finding_handle,
)
from autopatch_j.core.patching import (
    OldStringNotFoundError,
    OldStringNotUniqueError,
    SearchReplacePatchDraft,
    SyntaxCheckResult,
    TargetFileNotFoundError,
    UnsafeSourceEncodingError,
)
from autopatch_j.tools.contract import ToolExecutionResult, ToolRuntimeContext


@dataclass(frozen=True, slots=True)
class PatchDraftAction:
    action_label: str
    focus_verb: str


@dataclass(frozen=True, slots=True)
class _DraftAssociation:
    finding_id: str
    scan_id: str
    target_finding: FindingIdentity
    resolved_snippet: str | None


class SearchReplaceDraftBuilder:
    """
    search-replace 补丁草稿的共享生成器。

    propose_patch 和 revise_patch 的流程语义不同，但路径约束、finding 绑定、
    old_string 错误归一化和语法校验应保持一致。
    """

    def __init__(self, context: ToolRuntimeContext) -> None:
        self.context = context

    def build(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        associated_finding_id: str | None,
        action: PatchDraftAction,
    ) -> SearchReplacePatchDraft | ToolExecutionResult:
        focus_error = self._validate_focus(file_path, associated_finding_id, action)
        if focus_error is not None:
            return focus_error

        association: _DraftAssociation | None = None
        if associated_finding_id:
            associated_result = self._resolve_associated_finding(associated_finding_id, file_path)
            if isinstance(associated_result, ToolExecutionResult):
                return associated_result
            finding = associated_result.finding
            resolved_snippet = self.context.code_fetcher.fetch_resolved_snippet(
                file_path=finding.path,
                start_line=finding.region.start_line,
                end_line=finding.region.inclusive_end_line,
                fallback_snippet=finding.snippet,
            )
            association = _DraftAssociation(
                finding_id=associated_result.finding_id,
                scan_id=associated_result.scan_id,
                target_finding=finding.identity,
                resolved_snippet=resolved_snippet,
            )

        return self._build_with_association(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            rationale=rationale,
            action=action,
            association=association,
        )

    def build_revision(
        self,
        *,
        current_draft: SearchReplacePatchDraft,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        associated_finding_id: str | None,
        action: PatchDraftAction,
    ) -> SearchReplacePatchDraft | ToolExecutionResult:
        focus_error = self._validate_focus(file_path, associated_finding_id, action)
        if focus_error is not None:
            return focus_error
        if current_draft.error_code == "STALE_DRAFT":
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=current_draft.associated_finding_id,
                error_code="STALE_DRAFT",
                error_message=current_draft.message,
                resolved_snippet=None,
                message=current_draft.message,
                summary=f"补丁修订失败 (binding 已失效): {file_path}",
            )

        requested_path = self.context.normalize_repo_path(file_path)
        current_path = self.context.normalize_repo_path(current_draft.file_path)
        if requested_path != current_path:
            return self._build_error_result(
                file_path=requested_path,
                associated_finding_id=associated_finding_id,
                error_code="REVISION_FILE_MISMATCH",
                error_message="修订文件与当前待审补丁不一致。",
                resolved_snippet=None,
                message=(
                    f"补丁修订失败：当前补丁目标是 {current_path}，"
                    f"不能切换为 {requested_path}。"
                ),
                summary=f"补丁修订失败 (文件不匹配): {requested_path}",
            )

        normalized_finding_id: str | None = None
        if associated_finding_id is not None:
            try:
                normalized_finding_id, _ = parse_finding_handle(associated_finding_id)
            except FindingLookupError as exc:
                return self._build_error_result(
                    file_path=requested_path,
                    associated_finding_id=associated_finding_id,
                    error_code=exc.code,
                    error_message=str(exc),
                    resolved_snippet=None,
                    message=f"补丁修订失败：{exc}",
                    summary=f"补丁修订失败 (finding handle 无效): {requested_path}",
                )

        current_finding_id = current_draft.associated_finding_id
        if normalized_finding_id is not None and normalized_finding_id != current_finding_id:
            return self._revision_association_mismatch(
                file_path=requested_path,
                associated_finding_id=normalized_finding_id,
            )

        association: _DraftAssociation | None = None
        if current_finding_id is not None:
            assert current_draft.source_scan_id is not None
            assert current_draft.target_finding is not None
            target = current_draft.target_finding
            resolved_snippet = self.context.code_fetcher.fetch_resolved_snippet(
                file_path=target.path,
                start_line=target.region.start_line,
                end_line=target.region.inclusive_end_line,
                fallback_snippet=current_draft.old_string,
            )
            association = _DraftAssociation(
                finding_id=current_finding_id,
                scan_id=current_draft.source_scan_id,
                target_finding=target,
                resolved_snippet=resolved_snippet,
            )

        return self._build_with_association(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            rationale=rationale,
            action=action,
            association=association,
        )

    def _build_with_association(
        self,
        *,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        action: PatchDraftAction,
        association: _DraftAssociation | None,
    ) -> SearchReplacePatchDraft | ToolExecutionResult:
        resolved_associated_finding_id = association.finding_id if association else None
        resolved_snippet = association.resolved_snippet if association else None

        try:
            build_result = self.context.patch_engine.create_draft(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            )
        except TargetFileNotFoundError as exc:
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=resolved_associated_finding_id,
                error_code="FILE_NOT_FOUND",
                error_message=str(exc),
                resolved_snippet=resolved_snippet,
                message=f"{action.action_label}生成失败：{exc}",
                summary=f"补丁生成失败 (找不到文件): {file_path}",
            )
        except OldStringNotFoundError as exc:
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=resolved_associated_finding_id,
                error_code="OLD_STRING_NOT_FOUND",
                error_message=str(exc),
                resolved_snippet=resolved_snippet,
                message=f"{action.action_label}生成失败：{exc}",
                summary=f"补丁生成失败 (old_string 失配): {file_path}",
            )
        except OldStringNotUniqueError as exc:
            error_message = f"old_string 匹配了 {exc.occurrences} 处，匹配不唯一。"
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=resolved_associated_finding_id,
                error_code="OLD_STRING_NOT_UNIQUE",
                error_message=error_message,
                resolved_snippet=resolved_snippet,
                message=f"{action.action_label}生成失败：{error_message}",
                summary=f"补丁生成失败 (old_string 不唯一): {file_path}",
            )
        except UnsafeSourceEncodingError as exc:
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=resolved_associated_finding_id,
                error_code="UNSAFE_SOURCE_ENCODING",
                error_message=str(exc),
                resolved_snippet=resolved_snippet,
                message=f"{action.action_label}生成失败：{exc}",
                summary=f"补丁生成失败 (源码编码不安全): {file_path}",
            )

        if association is not None and not build_result.match_region.intersects(
            association.target_finding.region
        ):
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=association.finding_id,
                error_code="PATCH_OUTSIDE_FINDING_REGION",
                error_message="old_string 的源码区域未覆盖目标 finding。",
                resolved_snippet=resolved_snippet,
                message=(
                    f"{action.action_label}生成失败：补丁没有修改关联 finding "
                    f"{association.finding_id} 的证据区域。"
                ),
                summary=f"补丁生成失败 (未覆盖 finding): {file_path}",
            )

        validation_result = self._verify_syntax(file_path, build_result.updated_source)
        if validation_result.status == "unavailable":
            status = "unavailable"
        elif validation_result.status in {"ok", "skipped"}:
            status = "ok"
        else:
            status = "invalid"

        message_status = (
            "补丁起草成功并已通过语法校验。" if status == "ok" else validation_result.message
        )
        return SearchReplacePatchDraft(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            diff=build_result.diff,
            match_region=build_result.match_region,
            validation=validation_result,
            status=status,
            message=message_status,
            rationale=rationale,
            source_hint=self.context.patch_source_hint,
            error_code=None,
            associated_finding_id=association.finding_id if association else None,
            source_scan_id=association.scan_id if association else None,
            target_finding=association.target_finding if association else None,
        )

    def _validate_focus(
        self,
        file_path: str,
        associated_finding_id: str | None,
        action: PatchDraftAction,
    ) -> ToolExecutionResult | None:
        if self.context.is_path_in_focus(file_path):
            return None
        allowed = ", ".join(self.context.focus_paths)
        return ToolExecutionResult(
            status="error",
            message=(
                f"焦点约束阻止越界{action.focus_verb}：{file_path} "
                f"不在当前允许范围内。允许路径：{allowed}"
            ),
            summary=f"{action.focus_verb}越界: {file_path}",
            payload={
                "file_path": file_path,
                "associated_finding_id": associated_finding_id,
                "error_code": "OUT_OF_FOCUS",
                "error_message": "目标文件超出焦点范围。",
            },
        )

    def _revision_association_mismatch(
        self,
        *,
        file_path: str,
        associated_finding_id: str | None,
    ) -> ToolExecutionResult:
        return self._build_error_result(
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            error_code="REVISION_ASSOCIATION_MISMATCH",
            error_message="修订补丁不得切换当前 finding association。",
            resolved_snippet=None,
            message="补丁修订失败：不能切换当前补丁关联的 finding。",
            summary=f"补丁修订失败 (finding association 不匹配): {file_path}",
        )

    def _verify_syntax(self, file_path: str, new_code: str) -> SyntaxCheckResult:
        verifier = self.context.patch_verifier
        if verifier is None:
            return SyntaxCheckResult(status="unavailable", message="未配置补丁语法校验器。")
        return verifier.verify_syntax(file_path, new_code)

    def _build_error_result(
        self,
        *,
        file_path: str,
        associated_finding_id: str | None,
        error_code: str,
        error_message: str,
        resolved_snippet: str | None,
        message: str,
        summary: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            status="error",
            message=message,
            summary=summary,
            payload={
                "file_path": file_path,
                "associated_finding_id": associated_finding_id,
                "error_code": error_code,
                "error_message": error_message,
                "resolved_snippet": resolved_snippet,
            },
        )

    def _resolve_associated_finding(
        self,
        finding_id: str,
        file_path: str,
    ) -> FindingLookupResult | ToolExecutionResult:
        try:
            lookup = resolve_finding_handle(self.context.artifact_manager, self.context.workspace_manager, finding_id)
        except FindingLookupError as exc:
            return ToolExecutionResult(
                status="error",
                message=f"关联 finding 解析失败：{exc}",
                summary=f"关联 finding 解析失败: {finding_id}",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": finding_id,
                    "error_code": exc.code,
                    "error_message": str(exc),
                },
            )

        requested_path = self.context.normalize_repo_path(file_path)
        finding_path = self.context.normalize_repo_path(lookup.finding.path)
        if requested_path != finding_path:
            return ToolExecutionResult(
                status="error",
                message=(
                    f"关联 finding 文件不匹配：{lookup.finding_id} 属于 {finding_path}，"
                    f"但补丁目标是 {requested_path}。"
                ),
                summary=f"关联 finding 文件不匹配: {lookup.finding_id}",
                payload={
                    "file_path": requested_path,
                    "associated_finding_id": lookup.finding_id,
                    "source_scan_id": lookup.scan_id,
                    "error_code": "ASSOCIATED_FINDING_FILE_MISMATCH",
                    "error_message": "关联 finding 文件和补丁目标文件不一致。",
                },
            )
        return lookup
