from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from autopatch_j.core.models import (
    AuditAttemptOutcome,
    CodeScope,
    ConversationRoute,
    IntentType,
    PatchReviewItem,
)


from autopatch_j.config import GlobalConfig


@dataclass(slots=True)
class ChatInputDecision:
    """
    单次用户输入经过会话路由和意图识别后的决策。

    route 描述当前输入是命令、新任务还是继续审核；intent 描述后续要进入的业务工作流。
    该对象只承载分类结果，不执行任何副作用。
    """

    route: ConversationRoute
    intent: IntentType | None


class WorkflowControllerContext(Protocol):
    """
    WorkflowController 依赖的 CLI 能力协议。

    该协议刻意只描述 workflow 需要调用的能力，避免 workflow 层直接依赖完整 CLI 实现。
    """

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
    def _describe_scope_paths(self, scope: CodeScope) -> list[str]: ...
    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]: ...
    def _build_static_scan_summary(self) -> str: ...
    def _build_local_no_issue_summary(self) -> str: ...


class CliWorkflowController:
    """
    用户输入到业务工作流的编排器。

    职责边界：
    1. 负责 chat 输入的会话路由、意图分发、审计 backlog 推进和 pending patch 审核流程。
    2. 协调 ScopeService、ScannerRunner、BacklogManager、WorkspaceManager 和 Agent。
    3. 不直接执行工具、不直接修改文件内容；工具执行由 Agent 完成，补丁落盘由命令控制器和 PatchEngine 完成。
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
                    self.context.renderer.print_agent_text("补丁队列已清空")
            return

        if user_input.lower() == "discard":
            self.context.command_controller.handle_discard()
            with self.context.workspace_manager.edit() as workspace:
                workspace.mark_discarded()
                if not workspace.has_pending_patch():
                    self.context.renderer.print_agent_text("补丁队列已清空")
            return

        if user_input.lower() == "abort":
            self.context.workspace_manager.clear_workspace()
            self.context.agent.reset_history()
            self.context.renderer.print_agent_text("已中止审核流程，丢弃所有剩余补丁草案。")
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
            self.context.renderer.print_agent_text("请继续输入代码指令")
            return

        decision = self.classify_chat_input(text)
        if decision.route is ConversationRoute.COMMAND:
            self.context.command_controller.handle_command(text)
            return
        if decision.intent is None:
            self.handle_general_chat(text)
            return
        self.dispatch_chat_intent(text, decision.intent)

    def classify_chat_input(self, text: str) -> ChatInputDecision:
        workspace = self.context.workspace_manager.load_workspace()
        has_pending_review = workspace.has_pending_patch()
        requested_scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        current_item = workspace.get_current_patch() if has_pending_review else None
        route = self.context.conversation_router.determine_route(
            user_text=text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_item.file_path if current_item else None,
            current_scope=workspace.scope if has_pending_review else None,
        )

        if route is ConversationRoute.COMMAND:
            return ChatInputDecision(route=route, intent=None)

        if route is ConversationRoute.NEW_TASK:
            self.switch_to_new_task_if_needed(has_pending_review)
            intent = self.context.intent_detector.detect_intent(text, has_pending_review=False)
        else:
            intent = self.context.intent_detector.detect_intent(text, has_pending_review=True)
            if intent is IntentType.CODE_EXPLAIN and has_pending_review and "@" not in text:
                intent = IntentType.PATCH_EXPLAIN
        return ChatInputDecision(route=route, intent=intent)

    def switch_to_new_task_if_needed(self, has_pending_review: bool) -> None:
        self.context.agent.reset_history()
        if has_pending_review:
            self.context.workspace_manager.clear_workspace()
            self.context.renderer.print_agent_text("已切换到新任务")

    def dispatch_chat_intent(self, text: str, intent: IntentType) -> None:
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
                    **kwargs
                )
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

    def handle_code_explain(self, text: str) -> None:
        assert self.context.scope_service is not None
        assert self.context.agent is not None
        assert self.context.chat_filter is not None

        scope = self.context.scope_service.fetch_scope(text, default_to_project=False)
        compact_observation = not GlobalConfig.debug_mode
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
        
        self.context.agent.session.set_focus_paths([])
        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_general_chat(
                raw_user_text=text,
                **kwargs
            ),
            compact_observation=not GlobalConfig.debug_mode,
            answer_intent=IntentType.GENERAL_CHAT,
            raw_user_text=text,
            show_chat_anchors=True,
            plain_answer=True,
        )

    def handle_patch_explain(self, text: str) -> None:
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None

        current_item = self.context.workspace_manager.load_workspace().get_current_patch()
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
            answer_intent=IntentType.PATCH_EXPLAIN,
            raw_user_text=text,
        )

    def handle_patch_revise(self, text: str) -> None:
        assert self.context.workspace_manager is not None
        assert self.context.agent is not None

        current_item = self.context.workspace_manager.load_workspace().get_current_patch()
        if current_item is None:
            self.context.renderer.print_error("当前没有待确认补丁")
            return

        self.context.agent.session.set_focus_paths(self.context._fetch_review_scope_paths(current_item))
        self.context.agent.session.revised_patch_draft = None

        self.context._run_agent_request(
            prompt=text,
            agent_call=lambda p, **kwargs: self.context.agent.perform_patch_revise(
                raw_user_text=text,
                current_item=current_item,
                **kwargs
            ),
        )
        revised_patch = self.context.agent.session.pop_revised_patch_draft()
        if revised_patch is None:
            self.context.renderer.print_agent_text("未生成修订补丁，当前补丁保持不变。")
            return
        self.context.workspace_manager.replace_current_patch(revised_patch)
        self.context.renderer.print_agent_text("已更新当前补丁，后续补丁保持不变。")

    def _handle_zero_finding_review(self, text: str, scope: CodeScope) -> None:
        assert self.context.agent is not None
        assert self.context.workspace_manager is not None

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
                        **kwargs
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
