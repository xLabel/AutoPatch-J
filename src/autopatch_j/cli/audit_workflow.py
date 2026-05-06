from __future__ import annotations

from typing import Any

from autopatch_j.cli.workflow_types import WorkflowControllerContext
from autopatch_j.core.models import AuditAttemptOutcome, AuditFindingItem, CodeScope


class CliAuditWorkflow:
    """
    代码审计工作流编排器。

    职责边界：
    1. 负责 code_audit 的 scope 解析、静态扫描、finding backlog 推进和 retry。
    2. 负责把 Agent 暂存的补丁草案提交到 workspace 待确认队列。
    3. 不处理普通聊天路由，也不处理用户 apply/discard 的补丁确认动作。
    """

    def __init__(self, context: WorkflowControllerContext) -> None:
        self.context = context

    def handle_code_audit(self, text: str) -> None:
        backlog = self._prepare_audit_workspace(text)
        if backlog is None:
            return

        while finding := self.context.backlog_manager.fetch_current_finding(backlog):
            self._process_single_finding(finding, text, backlog)

        if not self.context.workspace_manager.load_workspace().has_pending_patch():
            self.context.workspace_manager.clear_workspace()

    def _prepare_audit_workspace(self, text: str) -> list[AuditFindingItem] | None:
        if not all(
            [
                self.context.scope_service,
                self.context.scanner_runner,
                self.context.workspace_manager,
                self.context.agent,
                self.context.backlog_manager,
            ]
        ):
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return None

        scope = self.context.scope_service.fetch_scope(text, default_to_project=True)
        if scope is None:
            self.context.renderer.print_error("未解析到可检查范围")
            return None

        self.context.agent.session.set_focus_paths(scope.focus_files if scope.is_locked else [])
        try:
            self.context.renderer.print_tool_start("scan_project", caller="AGENT")
            scan_id, scan_result = self.context.scanner_runner.run_scan_and_save(scope)
        except RuntimeError as exc:
            self.context.renderer.print_error(str(exc))
            return None

        self.context.workspace_manager.initialize_review_workspace(scope=scope, latest_scan_id=scan_id, patch_items=[])
        backlog = self.context.backlog_manager.fetch_backlog(scan_result)
        if not backlog:
            self._handle_zero_finding_review(text=text, scope=scope)
            if not self.context.workspace_manager.load_workspace().has_pending_patch():
                self.context.workspace_manager.clear_workspace()
            return None

        return backlog

    def _process_single_finding(
        self,
        finding: AuditFindingItem,
        text: str,
        backlog: list[AuditFindingItem],
    ) -> None:
        self._handle_finding_attempt(
            finding=finding,
            text=text,
            backlog=backlog,
            force_reread=False,
            allow_retry=True,
        )

    def _handle_finding_retry(
        self,
        finding: AuditFindingItem,
        text: str,
        backlog: list[AuditFindingItem],
    ) -> None:
        self._handle_finding_attempt(
            finding=finding,
            text=text,
            backlog=backlog,
            force_reread=True,
            allow_retry=False,
        )

    def _handle_finding_attempt(
        self,
        finding: AuditFindingItem,
        text: str,
        backlog: list[AuditFindingItem],
        *,
        force_reread: bool,
        allow_retry: bool,
    ) -> None:
        messages = self._run_finding_agent_attempt(finding, text, force_reread=force_reread)
        decision = self.context.backlog_manager.infer_attempt_decision(finding, messages)

        if decision.outcome is AuditAttemptOutcome.PATCH_READY:
            self._commit_or_mark_failed(backlog, finding)
            return

        if allow_retry and decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR and finding.retry_count < 1:
            self.context.backlog_manager.record_retry(
                backlog=backlog,
                finding_id=finding.finding_id,
                error_code=decision.error_code,
                error_message=decision.error_message,
            )
            self._handle_finding_retry(finding, text, backlog)
            return

        self._mark_finding_failed(
            backlog=backlog,
            finding=finding,
            error_code=decision.error_code,
            error_message=decision.error_message,
        )

    def _run_finding_agent_attempt(
        self,
        finding: AuditFindingItem,
        text: str,
        *,
        force_reread: bool,
    ) -> list[dict[str, Any]]:
        self.context.agent.reset_history()
        self.context.agent.session.clear_proposed_patch_draft()
        try:
            return self.context._run_agent_request(
                prompt=text,
                agent_call=lambda p, **kwargs: self.context.agent.perform_code_audit(
                    raw_user_text=text,
                    current_finding=finding,
                    force_reread=force_reread,
                    **kwargs,
                ),
                suppress_answer_output=True,
            ) or []
        except Exception:
            self.context.agent.session.clear_proposed_patch_draft()
            raise

    def _commit_or_mark_failed(self, backlog: list[AuditFindingItem], finding: AuditFindingItem) -> None:
        if self._commit_proposed_patch():
            self.context.backlog_manager.mark_patch_ready(backlog, finding.finding_id)
        else:
            self._mark_finding_failed(
                backlog=backlog,
                finding=finding,
                error_code="NO_PROPOSED_PATCH_DRAFT",
                error_message="propose_patch did not leave a patch draft for workflow commit.",
            )

    def _mark_finding_failed(
        self,
        *,
        backlog: list[AuditFindingItem],
        finding: AuditFindingItem,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        self.context.agent.session.clear_proposed_patch_draft()
        self.context.backlog_manager.mark_failed(
            backlog=backlog,
            finding_id=finding.finding_id,
            error_code=error_code,
            error_message=error_message,
        )

    def _commit_proposed_patch(self) -> bool:
        draft = self.context.agent.session.pop_proposed_patch_draft()
        if draft is None:
            return False
        self.context.workspace_manager.add_pending_patch(draft)
        return True

    def _handle_zero_finding_review(self, text: str, scope: CodeScope) -> None:
        if self.context.agent is None or self.context.workspace_manager is None:
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        for file_path in scope.focus_files:
            self.context.agent.reset_history()
            self.context.agent.session.set_focus_paths([file_path])
            self.context.agent.session.clear_proposed_patch_draft()
            self.context.agent.session.patch_source_hint = "LLM 二次复核（静态扫描未报出问题）"
            try:
                self.context._run_agent_request(
                    prompt=text,
                    agent_call=lambda p, **kwargs: self.context.agent.perform_zero_finding_review(
                        raw_user_text=text,
                        file_path=file_path,
                        **kwargs,
                    ),
                    scope_paths=[file_path],
                    compact_observation=True,
                    suppress_answer_output=True,
                )
            except Exception:
                self.context.agent.session.clear_proposed_patch_draft()
                raise
            finally:
                self.context.agent.session.patch_source_hint = None
            if self._commit_proposed_patch():
                return

        self.context.renderer.print_no_issue_panel(
            scope_paths=self.context._describe_scope_paths(scope),
            scanner_summary=self.context._build_static_scan_summary(),
            llm_summary=self.context._build_local_no_issue_summary(),
        )
        self.context.renderer.print()
