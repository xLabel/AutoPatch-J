from __future__ import annotations

from typing import Any

from autopatch_j.cli.workflow_dependencies import WorkflowDependencies
from autopatch_j.core.domain import AuditAttemptOutcome, FindingTask, CodeScope


class CodeAuditWorkflow:
    """
    代码审计工作流。

    负责 code_audit 的 scope 解析、静态扫描、finding backlog 推进、retry 和
    Agent 暂存补丁草案提交。
    """

    def __init__(self, services: WorkflowDependencies) -> None:
        self.services = services

    def handle_code_audit(self, text: str) -> None:
        backlog = self._prepare_audit_workspace(text)
        if backlog is None:
            return

        runtime = self.services.runtime
        while finding := runtime.backlog_manager.current(backlog):
            self._process_single_finding(finding, text, backlog)

        if not runtime.workspace_manager.load().has_pending_patch():
            runtime.workspace_manager.clear()

    def _prepare_audit_workspace(self, text: str) -> list[FindingTask] | None:
        runtime = self.services.runtime
        scope = runtime.scope_service.resolve(text, default_to_project=True)
        if scope is None:
            self.services.renderer.print_error("未解析到可检查范围")
            return None

        runtime.agent.session.set_focus_paths(scope.focus_files if scope.is_locked else [])
        try:
            self.services.renderer.print_tool_start("scan_project", caller="AGENT")
            scan_id, scan_result = runtime.scanner_runner.run_scan_and_save(scope)
        except RuntimeError as exc:
            self.services.renderer.print_error(str(exc))
            return None

        runtime.workspace_manager.initialize_review(scope=scope, latest_scan_id=scan_id, patch_items=[])
        backlog = runtime.backlog_manager.build_from_scan_result(scan_result)
        if not backlog:
            self._handle_zero_finding_review(text=text, scope=scope)
            if not runtime.workspace_manager.load().has_pending_patch():
                runtime.workspace_manager.clear()
            return None

        return backlog

    def _process_single_finding(
        self,
        finding: FindingTask,
        text: str,
        backlog: list[FindingTask],
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
        finding: FindingTask,
        text: str,
        backlog: list[FindingTask],
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
        finding: FindingTask,
        text: str,
        backlog: list[FindingTask],
        *,
        force_reread: bool,
        allow_retry: bool,
    ) -> None:
        runtime = self.services.runtime
        messages = self._run_finding_agent_attempt(finding, text, force_reread=force_reread)
        decision = runtime.backlog_manager.infer_attempt_decision(finding, messages)

        if decision.outcome is AuditAttemptOutcome.PATCH_READY:
            self._commit_or_mark_failed(backlog, finding)
            return

        if allow_retry and decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR and finding.retry_count < 1:
            runtime.backlog_manager.record_retry(
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
        finding: FindingTask,
        text: str,
        *,
        force_reread: bool,
    ) -> list[dict[str, Any]]:
        runtime = self.services.runtime
        runtime.agent.reset_history()
        runtime.agent.session.clear_proposed_patch_draft()
        try:
            return self.services.agent_runner.run(
                prompt=text,
                agent_call=lambda p, **kwargs: runtime.agent.perform_code_audit(
                    raw_user_text=text,
                    current_finding=finding,
                    force_reread=force_reread,
                    **kwargs,
                ),
                suppress_answer_output=True,
            ) or []
        except Exception:
            runtime.agent.session.clear_proposed_patch_draft()
            raise

    def _commit_or_mark_failed(self, backlog: list[FindingTask], finding: FindingTask) -> None:
        if self._commit_proposed_patch(finding):
            self.services.runtime.backlog_manager.mark_patch_ready(backlog, finding.finding_id)
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
        backlog: list[FindingTask],
        finding: FindingTask,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        runtime = self.services.runtime
        runtime.agent.session.clear_proposed_patch_draft()
        runtime.backlog_manager.mark_failed(
            backlog=backlog,
            finding_id=finding.finding_id,
            error_code=error_code,
            error_message=error_message,
        )

    def _commit_proposed_patch(self, finding: FindingTask | None = None) -> bool:
        runtime = self.services.runtime
        draft = runtime.agent.session.pop_proposed_patch_draft()
        if draft is None:
            return False
        if finding is not None and draft.associated_finding_id != finding.finding_id:
            return False
        runtime.workspace_manager.add_patch(draft)
        return True

    def _handle_zero_finding_review(self, text: str, scope: CodeScope) -> None:
        runtime = self.services.runtime
        for file_path in scope.focus_files:
            runtime.agent.reset_history()
            runtime.agent.session.set_focus_paths([file_path])
            runtime.agent.session.clear_proposed_patch_draft()
            runtime.agent.session.patch_source_hint = "LLM 二次复核（静态扫描未报出问题）"
            try:
                self.services.agent_runner.run(
                    prompt=text,
                    agent_call=lambda p, **kwargs: runtime.agent.perform_zero_finding_review(
                        raw_user_text=text,
                        file_path=file_path,
                        **kwargs,
                    ),
                    scope_paths=[file_path],
                    compact_observation=True,
                    suppress_answer_output=True,
                )
            except Exception:
                runtime.agent.session.clear_proposed_patch_draft()
                raise
            finally:
                runtime.agent.session.patch_source_hint = None
            if self._commit_proposed_patch():
                return

        self.services.renderer.print_no_issue_panel(
            scope_paths=self.services.summary_provider.describe_scope_paths(scope),
            scanner_summary=self.services.summary_provider.build_static_scan_summary(),
            llm_summary=self.services.summary_provider.build_local_no_issue_summary(),
        )
        self.services.renderer.print_blank()
