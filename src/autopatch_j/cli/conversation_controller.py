from __future__ import annotations

import re
from typing import Any, Protocol

from autopatch_j.core.audit_backlog_service import AuditBacklogService
from autopatch_j.core.chat_service import ChatService
from autopatch_j.core.continuity_judge_service import ContinuityJudgeService
from autopatch_j.core.intent_service import IntentService
from autopatch_j.core.models import (
    AuditAttemptOutcome,
    CodeScope,
    ConversationRoute,
    IntentType,
    PatchReviewItem,
)
from autopatch_j.core.scan_service import ScanService
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.workflow_service import WorkflowService


class ConversationControllerContext(Protocol):
    renderer: Any
    agent: Any
    intent_service: IntentService | None
    continuity_judge_service: ContinuityJudgeService | None
    scope_service: ScopeService | None
    scan_service: ScanService | None
    workflow_service: WorkflowService | None
    audit_backlog_service: AuditBacklogService | None
    chat_service: ChatService | None

    def handle_command(self, raw_cmd: str) -> None: ...
    def handle_apply(self, pending: Any) -> None: ...
    def handle_discard(self) -> None: ...
    def _run_agent_request(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]: ...
    def _should_show_full_tool_output(self, text: str) -> bool: ...
    def _build_code_audit_prompt(self, text: str, current_finding: Any, force_reread: bool) -> str: ...
    def _build_zero_finding_review_prompt(self, text: str, file_path: str) -> str: ...
    def _build_code_explain_prompt(self, text: str, scope: CodeScope) -> str: ...
    def _build_patch_explain_prompt(self, current_item: PatchReviewItem, user_text: str) -> str: ...
    def _build_patch_revise_prompt(
        self,
        current_item: PatchReviewItem,
        remaining_items: list[PatchReviewItem],
        user_text: str,
    ) -> str: ...
    def _describe_scope_paths(self, scope: CodeScope) -> list[str]: ...
    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]: ...


class CliConversationController:
    """Route user text into audit, explain, chat, and patch review flows."""

    def __init__(self, context: ConversationControllerContext) -> None:
        self.context = context

    def handle_review_input(self, user_input: str, current_item: PatchReviewItem) -> None:
        current_draft = current_item.draft.fetch_patch_draft()
        assert self.context.workflow_service is not None

        if user_input.lower() == "apply":
            self.context.handle_apply(current_draft)
            self.context.workflow_service.persist_applied_current_patch()
            if not self.context.workflow_service.verify_has_pending_patch():
                self.context.renderer.print_info("补丁队列已清空")
            return

        if user_input.lower() == "discard":
            self.context.handle_discard()
            self.context.workflow_service.persist_discarded_current_patch()
            if not self.context.workflow_service.verify_has_pending_patch():
                self.context.renderer.print_info("补丁队列已清空")
            return

        self.handle_chat(user_input)

    def handle_chat(self, text: str) -> None:
        if not all(
            [
                self.context.agent,
                self.context.intent_service,
                self.context.continuity_judge_service,
                self.context.scope_service,
                self.context.scan_service,
                self.context.workflow_service,
            ]
        ):
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.context.renderer.print_info("请继续输入代码指令")
            return

        has_pending_review = self.context.workflow_service.verify_has_pending_patch()
        requested_scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        current_item = self.context.workflow_service.fetch_current_patch_item() if has_pending_review else None
        current_workspace = self.context.workflow_service.fetch_workspace() if has_pending_review else None
        route = self.context.continuity_judge_service.fetch_route(
            user_text=text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_item.file_path if current_item else None,
            current_scope=current_workspace.scope if current_workspace else None,
        )

        if route is ConversationRoute.COMMAND:
            self.context.handle_command(text)
            return

        if route is ConversationRoute.NEW_TASK:
            self.context.agent.reset_history()
            if has_pending_review:
                self.context.workflow_service.clear_workspace()
                self.context.renderer.print_info("已切换到新任务")
            intent = self.context.intent_service.fetch_intent(text, has_pending_review=False)
        else:
            intent = self.context.intent_service.fetch_intent(text, has_pending_review=True)

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
        assert self.context.scope_service is not None
        assert self.context.scan_service is not None
        assert self.context.workflow_service is not None
        assert self.context.agent is not None
        assert self.context.audit_backlog_service is not None

        scope = self.context.scope_service.fetch_scope(text, default_to_project=True)
        if scope is None:
            self.context.renderer.print_error("未解析到可检查范围")
            return

        self.context.agent.set_focus_paths(scope.focus_files if scope.is_locked else [])
        try:
            self.context.renderer.print_tool_start("scan_project", caller="AGENT")
            scan_id, scan_result = self.context.scan_service.run_scan_and_persist(scope)
        except RuntimeError as exc:
            self.context.renderer.print_error(str(exc))
            return

        self.context.workflow_service.persist_review_workspace(scope=scope, latest_scan_id=scan_id, patch_items=[])
        backlog = self.context.audit_backlog_service.fetch_backlog(scan_result)
        if not backlog:
            self._handle_zero_finding_review(text=text, scope=scope)
            if not self.context.workflow_service.verify_has_pending_patch():
                self.context.workflow_service.clear_workspace()
            return

        while self.context.audit_backlog_service.verify_has_pending_finding(backlog):
            current_finding = self.context.audit_backlog_service.fetch_current_finding(backlog)
            if current_finding is None:
                break

            self.context.agent.reset_history()
            prompt = self.context._build_code_audit_prompt(
                text=text,
                current_finding=current_finding,
                force_reread=False,
            )
            new_messages = self.context._run_agent_request(
                prompt=prompt,
                agent_call=self.context.agent.perform_code_audit,
            ) or []
            decision = self.context.audit_backlog_service.infer_attempt_decision(current_finding, new_messages)
            if decision.outcome is AuditAttemptOutcome.PATCH_READY:
                self.context.audit_backlog_service.persist_mark_patch_ready(backlog, current_finding.finding_id)
                continue

            if (
                decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR
                and self.context.audit_backlog_service.verify_can_retry(current_finding)
            ):
                self.context.audit_backlog_service.persist_mark_retry(
                    backlog=backlog,
                    finding_id=current_finding.finding_id,
                    error_code=decision.error_code,
                    error_message=decision.error_message,
                )
                self.context.agent.reset_history()
                retry_prompt = self.context._build_code_audit_prompt(
                    text=text,
                    current_finding=current_finding,
                    force_reread=True,
                )
                retry_messages = self.context._run_agent_request(
                    prompt=retry_prompt,
                    agent_call=self.context.agent.perform_code_audit,
                ) or []
                retry_decision = self.context.audit_backlog_service.infer_attempt_decision(current_finding, retry_messages)
                if retry_decision.outcome is AuditAttemptOutcome.PATCH_READY:
                    self.context.audit_backlog_service.persist_mark_patch_ready(backlog, current_finding.finding_id)
                else:
                    self.context.audit_backlog_service.persist_mark_failed(
                        backlog=backlog,
                        finding_id=current_finding.finding_id,
                        error_code=retry_decision.error_code,
                        error_message=retry_decision.error_message,
                    )
                continue

            self.context.audit_backlog_service.persist_mark_failed(
                backlog=backlog,
                finding_id=current_finding.finding_id,
                error_code=decision.error_code,
                error_message=decision.error_message,
            )

        if not self.context.workflow_service.verify_has_pending_patch():
            self.context.workflow_service.clear_workspace()

    def _handle_zero_finding_review(self, text: str, scope: CodeScope) -> None:
        assert self.context.agent is not None
        assert self.context.workflow_service is not None

        for file_path in scope.focus_files:
            self.context.agent.reset_history()
            self.context.agent.set_focus_paths([file_path])
            self.context.agent.set_patch_source_hint("LLM 二次复核（静态扫描未报出问题）")
            prompt = self.context._build_zero_finding_review_prompt(text=text, file_path=file_path)
            try:
                self.context._run_agent_request(
                    prompt=prompt,
                    agent_call=self.context.agent.perform_zero_finding_review,
                    scope_paths=[file_path],
                    compact_observation=True,
                    suppress_answer_output=True,
                )
            finally:
                self.context.agent.set_patch_source_hint(None)
            if self.context.workflow_service.verify_has_pending_patch():
                return

        self.context.renderer.print_no_issue_panel(
            scope_paths=self.context._describe_scope_paths(scope),
            scanner_summary=self.context._build_static_scan_summary(),
            llm_summary=self.context._build_local_no_issue_summary(),
        )
        self.context.renderer.print()

    def handle_code_explain(self, text: str) -> None:
        assert self.context.scope_service is not None
        assert self.context.agent is not None
        assert self.context.chat_service is not None

        scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        compact_observation = not self.context._should_show_full_tool_output(text)
        self.context.renderer.print_user_anchor(text)
        if scope is not None and scope.is_locked:
            self.context.agent.set_focus_paths(scope.focus_files)
            prompt = self.context._build_code_explain_prompt(text=text, scope=scope)
            allow_symbol_search = scope.kind.value != "single_file"
            self.context.agent.set_code_explain_symbol_search_enabled(allow_symbol_search)
            self.context._run_agent_request(
                prompt=prompt,
                agent_call=self.context.agent.perform_code_explain,
                compact_observation=compact_observation,
                answer_intent=IntentType.CODE_EXPLAIN,
                raw_user_text=text,
                show_chat_anchors=True,
                plain_answer=True,
            )
            return

        self.context.agent.set_focus_paths([])
        self.context.agent.set_code_explain_symbol_search_enabled(True)
        self.context._run_agent_request(
            prompt=text,
            agent_call=self.context.agent.perform_general_chat,
            compact_observation=compact_observation,
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            show_chat_anchors=True,
            plain_answer=True,
        )

    def handle_general_chat(self, text: str) -> None:
        assert self.context.agent is not None
        assert self.context.chat_service is not None
        self.context.renderer.print_user_anchor(text)
        if not self.context.chat_service.verify_programming_related(text):
            self.context.renderer.print_assistant_anchor()
            self.context.renderer.print_plain(self.context.chat_service.fetch_out_of_scope_reply())
            return
        self.context.agent.set_focus_paths([])
        self.context._run_agent_request(
            prompt=text,
            agent_call=self.context.agent.perform_general_chat,
            compact_observation=not self.context._should_show_full_tool_output(text),
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            show_chat_anchors=True,
            plain_answer=True,
        )

    def handle_patch_explain(self, text: str) -> None:
        assert self.context.workflow_service is not None
        assert self.context.agent is not None

        current_item = self.context.workflow_service.fetch_current_patch_item()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self.context._fetch_review_scope_paths(current_item)
        self.context.agent.set_focus_paths(focus_paths)
        prompt = self.context._build_patch_explain_prompt(current_item=current_item, user_text=text)
        self.context._run_agent_request(
            prompt=prompt,
            agent_call=self.context.agent.perform_patch_explain,
        )

    def handle_patch_revise(self, text: str) -> None:
        assert self.context.workflow_service is not None
        assert self.context.agent is not None

        current_item = self.context.workflow_service.fetch_current_patch_item()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        remaining_items = self.context.workflow_service.fetch_remaining_patch_items()
        prompt = self.context._build_patch_revise_prompt(
            current_item=current_item,
            remaining_items=remaining_items,
            user_text=text,
        )
        self.context.agent.set_focus_paths(self.context._fetch_review_scope_paths(current_item))
        self.context.workflow_service.replace_remaining_patch_items([])
        self.context._run_agent_request(
            prompt=prompt,
            agent_call=self.context.agent.perform_patch_revise,
        )
        if not self.context.workflow_service.verify_has_pending_patch():
            self.context.renderer.print_info("补丁队列已清空")
