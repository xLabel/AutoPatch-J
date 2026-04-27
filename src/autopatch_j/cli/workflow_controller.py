from __future__ import annotations

import re
from typing import Any, Protocol

from autopatch_j.core.backlog_manager import BacklogManager
from autopatch_j.core.chat_filter import ChatFilter
from autopatch_j.core.conversation_router import ConversationRouter
from autopatch_j.core.intent_detector import IntentDetector
from autopatch_j.core.models import (
    AuditAttemptOutcome,
    CodeScope,
    ConversationRoute,
    IntentType,
    PatchReviewItem,
)
from autopatch_j.core.scanner_runner import ScannerRunner
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.workspace_manager import WorkspaceManager


class WorkflowControllerContext(Protocol):
    renderer: Any
    agent: Any
    intent_detector: Any
    conversation_router: Any
    scope_service: Any
    scanner_runner: Any
    workspace_manager: Any
    backlog_manager: Any
    chat_filter: Any
    command_controller: Any

    def _run_agent_request(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    def _should_show_full_tool_output(self, text: str) -> bool: ...
    def _describe_scope_paths(self, scope: CodeScope) -> list[str]: ...
    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]: ...
    def _build_static_scan_summary(self) -> str: ...
    def _build_local_no_issue_summary(self) -> str: ...


class CliWorkflowController:
    """
    工作流总控与调度中心 (Workflow Orchestrator)。
    核心职责：接收原始输入，调用意图识别，并编排复杂的业务流。
    
    Typical process (e.g., code_audit):
    1. Resolve scope (Scope) -> 2. Trigger static scan (ScannerRunner) -> 
    3. Push to backlog (BacklogManager) -> 4. Drive Agent repair one by one (Agent) -> 
    5. Retry or skip on failure -> 6. Final human confirmation (WorkspaceManager).
    """

    def __init__(self, context: WorkflowControllerContext) -> None:
        self.context = context

    def handle_review_input(self, user_input: str, current_item: PatchReviewItem) -> None:
        current_draft = current_item.draft.fetch_patch_draft()
        assert self.context.workspace_manager is not None

        if user_input.lower() == "apply":
            self.context.command_controller.handle_apply(current_draft)
            with self.context.workspace_manager.edit() as workspace:
                workspace.mark_applied()
                if not workspace.has_pending_patch():
                    self.context.renderer.print_info("补丁队列已清空")
            return

        if user_input.lower() == "discard":
            self.context.command_controller.handle_discard()
            with self.context.workspace_manager.edit() as workspace:
                workspace.mark_discarded()
                if not workspace.has_pending_patch():
                    self.context.renderer.print_info("补丁队列已清空")
            return

        self.handle_chat(user_input)

    def handle_chat(self, text: str) -> None:
        if not all(
            [
                self.context.agent,
                self.context.intent_detector,
                self.context.conversation_router,
                self.context.scope_service,
                self.context.scanner_runner,
                self.context.workspace_manager,
            ]
        ):
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.context.renderer.print_info("请继续输入代码指令")
            return

        has_pending_review = self.context.workspace_manager.has_pending_patch()
        requested_scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        current_item = self.context.workspace_manager.get_current_patch() if has_pending_review else None
        current_workspace = self.context.workspace_manager.load_workspace() if has_pending_review else None
        route = self.context.conversation_router.determine_route(
            user_text=text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_item.file_path if current_item else None,
            current_scope=current_workspace.scope if current_workspace else None,
        )

        if route is ConversationRoute.COMMAND:
            self.context.command_controller.handle_command(text)
            return

        if route is ConversationRoute.NEW_TASK:
            self.context.agent.reset_history()
            if has_pending_review:
                with self.context.workspace_manager.edit() as workspace:
                    workspace.clear_workspace() # Oops, wait, WorkspaceManager.clear_workspace was kept but it is also proxy? No, it delegates to artifact_manager.
                self.context.renderer.print_info("已切换到新任务")
            intent = self.context.intent_detector.detect_intent(text, has_pending_review=False)
        else:
            intent = self.context.intent_detector.detect_intent(text, has_pending_review=True)

        if intent is IntentType.CODE_AUDIT:
            self.handle_code_audit(text)
            return
        if intent is IntentType.CODE_EXPLAIN:
            self.handle_code_explain(text)
            return
        if intent is IntentType.PATCH_EXPLAIN:
            self.handle_patch_explain(text)
            return
        if intent is IntentType.PATCH_REVISE:
            self.handle_patch_revise(text)
            return
        self.handle_general_chat(text)

    def handle_code_audit(self, text: str) -> None:
        backlog = self._prepare_audit_workspace(text)
        if backlog is None:
            return

        while finding := self.context.backlog_manager.fetch_current_finding(backlog):
            self._process_single_finding(finding, text, backlog)

        if not self.context.workspace_manager.load_workspace().has_pending_patch():
            self.context.workspace_manager.clear_workspace()

    def _prepare_audit_workspace(self, text: str) -> list[AuditFindingItem] | None:
        assert self.context.scope_service is not None
        assert self.context.scanner_runner is not None
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None
        assert self.context.backlog_manager is not None

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
            if not self.context.workspace_manager.has_pending_patch():
                self.context.workspace_manager.clear_workspace()
            return None

        return backlog

    def _process_single_finding(
        self,
        finding: AuditFindingItem,
        text: str,
        backlog: list[AuditFindingItem],
    ) -> None:
        self.context.agent.reset_history()
        new_messages = self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_code_audit(
                raw_user_text=text,
                current_finding=finding,
                force_reread=False,
                **kwargs
            )
        ) or []
        decision = self.context.backlog_manager.infer_attempt_decision(finding, new_messages)
        
        if decision.outcome is AuditAttemptOutcome.PATCH_READY:
            self.context.backlog_manager.mark_patch_ready(backlog, finding.finding_id)
            return

        if (
            decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR
            and finding.retry_count < 1
        ):
            self.context.backlog_manager.record_retry(
                backlog=backlog,
                finding_id=finding.finding_id,
                error_code=decision.error_code,
                error_message=decision.error_message,
            )
            self._handle_finding_retry(finding, text, backlog)
            return

        self.context.backlog_manager.mark_failed(
            backlog=backlog,
            finding_id=finding.finding_id,
            error_code=decision.error_code,
            error_message=decision.error_message,
        )

    def _handle_finding_retry(
        self,
        finding: AuditFindingItem,
        text: str,
        backlog: list[AuditFindingItem],
    ) -> None:
        self.context.agent.reset_history()
        retry_messages = self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_code_audit(
                raw_user_text=text,
                current_finding=finding,
                force_reread=True,
                **kwargs
            )
        ) or []
        retry_decision = self.context.backlog_manager.infer_attempt_decision(finding, retry_messages)
        
        if retry_decision.outcome is AuditAttemptOutcome.PATCH_READY:
            self.context.backlog_manager.mark_patch_ready(backlog, finding.finding_id)
        else:
            self.context.backlog_manager.mark_failed(
                backlog=backlog,
                finding_id=finding.finding_id,
                error_code=retry_decision.error_code,
                error_message=retry_decision.error_message,
            )

    def handle_code_explain(self, text: str) -> None:
        assert self.context.scope_service is not None
        assert self.context.agent is not None
        assert self.context.chat_filter is not None

        scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        compact_observation = not self.context._should_show_full_tool_output(text)
        self.context.renderer.print_user_anchor(text)
        if scope is not None and scope.is_locked:
            self.context.agent.session.set_focus_paths(scope.focus_files)
            allow_symbol_search = scope.kind.value != "single_file"
            self.context.agent.session.code_explain_allow_symbol_search = allow_symbol_search
            self.context._run_agent_request(
                prompt=text,
                agent_call=lambda p, **kwargs: self.context.agent.perform_code_explain(
                    raw_user_text=text,
                    scope=scope,
                    allow_symbol_search=allow_symbol_search,
                    **kwargs
                ),
                compact_observation=compact_observation,
                answer_intent=IntentType.CODE_EXPLAIN,
                raw_user_text=text,
                show_chat_anchors=True,
                plain_answer=True,
            )
            return

        self.context.agent.session.set_focus_paths([])
        self.context.agent.session.code_explain_allow_symbol_search = True
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs
            ),
            compact_observation=compact_observation,
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            show_chat_anchors=True,
            plain_answer=True,
        )

    def handle_general_chat(self, text: str) -> None:
        assert self.context.agent is not None
        assert self.context.chat_filter is not None
        self.context.renderer.print_user_anchor(text)
        if not self.context.chat_filter.verify_programming_related(text):
            self.context.renderer.print_assistant_anchor()
            self.context.renderer.print_plain(self.context.chat_filter.fetch_out_of_scope_reply())
            return
        self.context.agent.session.set_focus_paths([])
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs
            ),
            compact_observation=not self.context._should_show_full_tool_output(text),
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            show_chat_anchors=True,
            plain_answer=True,
        )

    def handle_patch_explain(self, text: str) -> None:
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None

        current_item = self.context.workspace_manager.get_current_patch()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self.context._fetch_review_scope_paths(current_item)
        self.context.agent.session.set_focus_paths(focus_paths)
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_patch_explain(
                raw_user_text=text,
                current_item=current_item,
                **kwargs
            ),
        )

    def handle_patch_revise(self, text: str) -> None:
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None

        current_item = self.context.workspace_manager.load_workspace().get_current_patch()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        remaining_items = self.context.workspace_manager.load_workspace().get_remaining_patches()
        self.context.agent.session.set_focus_paths(self.context._fetch_review_scope_paths(current_item))
        
        with self.context.workspace_manager.edit() as workspace:
            workspace.replace_tail([])

        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_patch_revise(
                raw_user_text=text,
                current_item=current_item,
                remaining_items=remaining_items,
                **kwargs
            ),
        )
        if not self.context.workspace_manager.load_workspace().has_pending_patch():
            self.context.renderer.print_info("补丁队列已清空")

    def _handle_zero_finding_review(self, text: str, scope: CodeScope) -> None:
        assert self.context.agent is not None
        assert self.context.workspace_manager is not None

        for file_path in scope.focus_files:
            self.context.agent.reset_history()
            self.context.agent.session.set_focus_paths([file_path])
            self.context.agent.session.patch_source_hint = "LLM 二次复核（静态扫描未报出问题）"
            try:
                self.context._run_agent_request(
                    prompt=text,
                    agent_call=lambda p, **kwargs: self.context.agent.perform_zero_finding_review(
                        raw_user_text=text,
                        file_path=file_path,
                        **kwargs
                    ),
                    scope_paths=[file_path],
                    compact_observation=True,
                    suppress_answer_output=True,
                )
            finally:
                self.context.agent.session.patch_source_hint = None
            if self.context.workspace_manager.load_workspace().has_pending_patch():
                return

        self.context.renderer.print_no_issue_panel(
            scope_paths=self.context._describe_scope_paths(scope),
            scanner_summary=self.context._build_static_scan_summary(),
            llm_summary=self.context._build_local_no_issue_summary(),
        )
        self.context.renderer.print()
