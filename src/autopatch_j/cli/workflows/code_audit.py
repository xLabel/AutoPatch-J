"""静态扫描、finding 顺序处理及暂存草案入队。"""

from __future__ import annotations

import functools

from autopatch_j import config
from autopatch_j.agent import result as agent_result
from autopatch_j.cli import workflow_dependencies
from autopatch_j.core.domain import audit
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.tools import contract


class _AuditScanError(RuntimeError):
    pass


class CodeAuditWorkflow:
    """代码审计工作流。

    负责 code_audit 的 scope 解析、静态扫描、finding backlog 推进、retry 和
    Agent 暂存补丁草案提交。
    """

    def __init__(
        self, services: workflow_dependencies.WorkflowDependencies
    ) -> None:
        self.services = services

    def handle_code_audit(self, text: str) -> None:
        """解析范围并执行扫描与草案流程，扫描失败不能冒充零 finding。"""
        runtime = self.services.runtime
        scope = runtime.scope_service.resolve(text, default_to_project=True)
        if scope is None:
            self.services.renderer.print_error("未解析到可检查范围")
            return
        try:
            summary = self._run_code_audit(text, scope)
        except _AuditScanError as exc:
            self.services.renderer.print_error(str(exc))
            return
        self.services.renderer.print_agent_text(summary)

    def _run_code_audit(self, text: str, scope: domain_scope.CodeScope) -> str:
        """执行有界 finding 批次或零 finding 复核，汇总草案及失败计数。"""
        backlog = self._prepare_audit_workspace(text, scope)
        if backlog is None:
            workspace = self.services.runtime.workspace_manager.load()
            patch_count = sum(
                1 for item in workspace.patch_items if item.is_pending()
            )
            return (
                "本轮代码审计结束：静态扫描未发现 finding，LLM 二次复核"
                f"生成 {patch_count} 个待审补丁。"
            )

        runtime = self.services.runtime
        processed_count = 0
        while finding := runtime.backlog_manager.current(backlog):
            if processed_count >= config.GLOBAL_CONFIG.audit_batch_limit:
                self._render_batch_limit_hint(backlog)
                break
            self._process_single_finding(finding, text, backlog)
            processed_count += 1

        if not runtime.workspace_manager.load().has_pending_patch():
            runtime.workspace_manager.clear()
        patch_count = sum(
            1
            for finding in backlog
            if finding.status is audit.AuditFindingStatus.PATCH_READY
        )
        failed_count = sum(
            1
            for finding in backlog
            if finding.status is audit.AuditFindingStatus.FAILED
        )
        remaining_count = sum(1 for finding in backlog if finding.is_pending())
        return (
            f"本轮代码审计结束：扫描发现 {len(backlog)} 个 fin"
            f"ding，"
            f"生成 {patch_count} 个待审补丁，{failed_count}"
            f" 个处理失败，"
            f"{remaining_count} 个留待后续处理。"
        )

    def _render_batch_limit_hint(
        self, backlog: list[audit.FindingTask]
    ) -> None:
        remaining = sum(1 for finding in backlog if finding.is_pending())
        if remaining <= 0:
            return
        self.services.renderer.print_agent_text(
            f"本轮已处理 {config.GLOBAL_CONFIG.audit_batch_limit}"
            f" 个 finding，仍有 {remaining} 个待处理。"
            "请确认当前补丁后再次发起检查继续处理。"
        )

    def _prepare_audit_workspace(
        self,
        text: str,
        scope: domain_scope.CodeScope,
    ) -> list[audit.FindingTask] | None:
        """成功扫描后建立审核范围，零 finding 时转入独立模型复核。"""
        runtime = self.services.runtime
        runtime.agent.session.set_focus_paths(
            scope.focus_files if scope.is_locked else []
        )
        try:
            self.services.renderer.print_tool_start(
                "scan_project", caller="AGENT"
            )
            scan_id, scan_result = runtime.scanner_runner.run_scan_and_save(
                scope
            )
        except RuntimeError as exc:
            raise _AuditScanError(str(exc)) from exc

        runtime.workspace_manager.initialize_review(
            scope=scope, latest_scan_id=scan_id, patch_items=[]
        )
        backlog = runtime.backlog_manager.build_from_scan_result(scan_result)
        if not backlog:
            self._handle_zero_finding_review(text=text, scope=scope)
            if not runtime.workspace_manager.load().has_pending_patch():
                runtime.workspace_manager.clear()
            return None

        return backlog

    def _process_single_finding(
        self,
        finding: audit.FindingTask,
        text: str,
        backlog: list[audit.FindingTask],
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
        finding: audit.FindingTask,
        text: str,
        backlog: list[audit.FindingTask],
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
        finding: audit.FindingTask,
        text: str,
        backlog: list[audit.FindingTask],
        *,
        force_reread: bool,
        allow_retry: bool,
    ) -> None:
        """按真实工具观察决定提交、一次强制重读或记录失败。"""
        runtime = self.services.runtime
        messages = self._run_finding_agent_attempt(
            finding, text, force_reread=force_reread
        )
        decision = runtime.backlog_manager.infer_attempt_decision(
            finding, messages
        )

        if decision.outcome is audit.AuditAttemptOutcome.PATCH_READY:
            self._commit_or_mark_failed(backlog, finding)
            return

        if (
            allow_retry
            and decision.outcome is audit.AuditAttemptOutcome.RETRYABLE_ERROR
            and finding.retry_count < 1
        ):
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
        finding: audit.FindingTask,
        text: str,
        *,
        force_reread: bool,
    ) -> tuple[contract.ToolExecutionResult, ...]:
        """隔离运行单个 finding；取消中断流程，失败不能冒充成功。"""
        runtime = self.services.runtime
        runtime.agent.reset_history()
        runtime.agent.session.clear_proposed_patch_draft()
        try:
            result = self.services.agent_runner.run(
                prompt=text,
                agent_call=functools.partial(
                    runtime.agent.perform_code_audit,
                    current_finding=finding,
                    force_reread=force_reread,
                ),
                suppress_answer_output=True,
            )
            if result.status is agent_result.RunStatus.CANCELLED:
                raise KeyboardInterrupt
            if result.status is agent_result.RunStatus.FAILED:
                raise _AuditScanError(result.raw_answer)
            return result.observations
        except Exception:
            runtime.agent.session.clear_proposed_patch_draft()
            raise

    def _commit_or_mark_failed(
        self, backlog: list[audit.FindingTask], finding: audit.FindingTask
    ) -> None:
        """有暂存草案则入队，否则将当前 finding 记录为失败。"""
        if self._commit_proposed_patch(finding):
            self.services.runtime.backlog_manager.mark_patch_ready(
                backlog, finding.finding_id
            )
        else:
            self._mark_finding_failed(
                backlog=backlog,
                finding=finding,
                error_code="NO_PROPOSED_PATCH_DRAFT",
                error_message="propose_patch did not leave a patch"
                " draft for workflow commit.",
            )

    def _mark_finding_failed(
        self,
        *,
        backlog: list[audit.FindingTask],
        finding: audit.FindingTask,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        """清除未提交草案并记录当前 finding 的失败原因。"""
        runtime = self.services.runtime
        runtime.agent.session.clear_proposed_patch_draft()
        runtime.backlog_manager.mark_failed(
            backlog=backlog,
            finding_id=finding.finding_id,
            error_code=error_code,
            error_message=error_message,
        )

    def _commit_proposed_patch(
        self, finding: audit.FindingTask | None = None
    ) -> bool:
        """取出暂存草案并核对当前 finding，成功后才加入审核队列。"""
        runtime = self.services.runtime
        draft = runtime.agent.session.pop_proposed_patch_draft()
        if draft is None:
            return False
        if (
            finding is not None
            and draft.associated_finding_id != finding.finding_id
        ):
            return False
        runtime.workspace_manager.add_patch(draft)
        return True

    def _handle_zero_finding_review(
        self, text: str, scope: domain_scope.CodeScope
    ) -> None:
        """逐文件复核，首个草案入队后停止；未完成时不能显示无问题。"""
        runtime = self.services.runtime
        for file_path in scope.focus_files:
            runtime.agent.reset_history()
            runtime.agent.session.set_focus_paths([file_path])
            runtime.agent.session.clear_proposed_patch_draft()
            runtime.agent.session.patch_source_hint = (
                "LLM 二次复核（静态扫描未报出问题）"
            )
            try:
                response = self.services.agent_runner.run(
                    prompt=text,
                    agent_call=functools.partial(
                        runtime.agent.perform_zero_finding_review,
                        file_path=file_path,
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
            if response.status is agent_result.RunStatus.CANCELLED:
                raise KeyboardInterrupt
            if self._commit_proposed_patch():
                return
            if response.status is not agent_result.RunStatus.COMPLETED:
                raise _AuditScanError(
                    "二次复核未完整完成，不能据此显示未发现问题。"
                    + response.raw_answer
                )

        summaries = self.services.summary_provider
        self.services.renderer.print_no_issue_panel(
            scope_paths=summaries.describe_scope_paths(scope),
            scanner_summary=summaries.build_static_scan_summary(),
            llm_summary=summaries.build_local_no_issue_summary(),
        )
        self.services.renderer.print_blank()
