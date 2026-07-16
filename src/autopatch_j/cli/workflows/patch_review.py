from __future__ import annotations

from autopatch_j.cli.workflow_dependencies import WorkflowDependencies
from autopatch_j.core.domain import (
    IntentType,
    PatchDraftSnapshot,
    PatchReviewStatus,
    ReviewPatchItem,
    ReviewWorkspace,
)
from autopatch_j.core.finding import SourceRegion
from autopatch_j.core.patching import SyntaxCheckResult
from autopatch_j.core.patching.types import normalize_patch_path


class PatchReviewWorkflow:
    """
    待确认补丁工作流。

    负责 pending patch 期间的 apply、discard、abort、patch_explain 和
    patch_revise。它不反向调用主路由；无法消费的普通文本由 UserInputRouter 再分类。
    """

    def __init__(self, services: WorkflowDependencies) -> None:
        self.services = services

    def handle_review_action(self, user_input: str, current_item: ReviewPatchItem) -> bool:
        runtime = self.services.runtime
        current_draft = current_item.draft.to_patch_draft()
        normalized = user_input.lower()

        if normalized == "apply":
            apply_result = self.services.command_handlers.handle_apply(current_draft)
            if not apply_result.applied:
                return True
            assert apply_result.source_region is not None
            assert apply_result.changed_region is not None
            rebased_count = 0
            stale_count = 0
            with runtime.workspace_manager.edit() as workspace:
                workspace.mark_current_patch_applied()
                rebased_count, stale_count = self._rebase_pending_same_file_patches(
                    workspace=workspace,
                    file_path=current_draft.file_path,
                    source_region=apply_result.source_region,
                    changed_region=apply_result.changed_region,
                )
                if not workspace.has_pending_patch():
                    self.services.renderer.print_agent_text("补丁队列已清空")
            if rebased_count or stale_count:
                self.services.renderer.print_agent_text(
                    f"已重定位 {rebased_count} 个同文件待审补丁；"
                    f"{stale_count} 个补丁需重新扫描。"
                )
            return True

        if normalized == "discard":
            self.services.command_handlers.handle_discard()
            with runtime.workspace_manager.edit() as workspace:
                workspace.mark_current_patch_discarded()
                if not workspace.has_pending_patch():
                    self.services.renderer.print_agent_text("补丁队列已清空")
            return True

        if normalized == "abort":
            runtime.workspace_manager.clear()
            runtime.agent.reset_history()
            self.services.renderer.print_agent_text("已中止审核流程，丢弃所有剩余补丁草案。")
            return True

        return False

    def handle_patch_explain(self, text: str) -> None:
        runtime = self.services.runtime
        current_item = runtime.workspace_manager.load().current_patch()
        if current_item is None:
            self.services.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self.services.summary_provider.fetch_review_scope_paths(current_item)
        runtime.agent.session.set_focus_paths(focus_paths)
        self.services.agent_runner.run(
            prompt=text,
            agent_call=lambda p, **kwargs: runtime.agent.perform_patch_explain(
                raw_user_text=text,
                current_item=current_item,
                **kwargs,
            ),
            answer_intent=IntentType.PATCH_EXPLAIN,
            raw_user_text=text,
        )

    def handle_patch_revise(self, text: str) -> None:
        runtime = self.services.runtime
        current_item = runtime.workspace_manager.load().current_patch()
        if current_item is None:
            self.services.renderer.print_error("当前没有待确认补丁")
            return
        if current_item.draft.error_code == "STALE_DRAFT":
            self.services.renderer.print_error(
                "当前补丁绑定已失效，不能继续修订；请 discard，或 abort 后重新扫描。"
            )
            return

        runtime.agent.session.set_focus_paths([current_item.file_path])
        runtime.agent.session.revised_patch_draft = None

        self.services.agent_runner.run(
            prompt=text,
            agent_call=lambda p, **kwargs: runtime.agent.perform_patch_revise(
                raw_user_text=text,
                current_item=current_item,
                **kwargs,
            ),
        )
        revised_patch = runtime.agent.session.pop_revised_patch_draft()
        if revised_patch is None:
            self.services.renderer.print_agent_text("未生成修订补丁，当前补丁保持不变。")
            return
        if not runtime.workspace_manager.replace_current_patch(revised_patch):
            self.services.renderer.print_error(
                "修订补丁未应用：当前 finding binding 校验失败，原补丁保持不变。"
            )
            return
        self.services.renderer.print_agent_text("已更新当前补丁，后续补丁保持不变。")

    def _rebase_pending_same_file_patches(
        self,
        *,
        workspace: ReviewWorkspace,
        file_path: str,
        source_region: SourceRegion,
        changed_region: SourceRegion,
    ) -> tuple[int, int]:
        runtime = self.services.runtime
        rebased_count = 0
        stale_count = 0
        normalized_file_path = normalize_patch_path(file_path)
        for item in workspace.patch_items:
            if item.status is not PatchReviewStatus.PENDING:
                continue
            if normalize_patch_path(item.file_path) != normalized_file_path:
                continue
            if item.draft.error_code == "STALE_DRAFT":
                continue
            try:
                result = runtime.patch_engine.rebase_draft(
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
                item.draft = PatchDraftSnapshot(
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
            except Exception as exc:
                item.draft.error_code = "STALE_DRAFT"
                item.draft.message = (
                    "待审补丁已失效：重定位发生异常："
                    f"{exc}。请 discard，或 abort 后重新扫描。"
                )
                stale_count += 1
        return rebased_count, stale_count

    def _verify_rebased_syntax(self, file_path: str, updated_source: str) -> SyntaxCheckResult:
        verifier = self.services.runtime.patch_verifier
        if verifier is None:
            return SyntaxCheckResult(status="unavailable", message="未配置补丁语法校验器。")
        return verifier.verify_syntax(file_path, updated_source)
