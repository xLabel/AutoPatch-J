from __future__ import annotations

import re
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from prompt_toolkit import HTML, PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.agent.llm_client import build_default_llm_client
from autopatch_j.cli.completer import AutoPatchCompleter
from autopatch_j.cli.render import (
    DECISION_STYLE,
    MUTED_STYLE,
    SYSTEM_STYLE,
    CliRenderer,
)
from autopatch_j.config import GlobalConfig, discover_repo_root, get_project_state_dir
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.audit_backlog_service import AuditBacklogService
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.continuity_judge_service import ContinuityJudgeService
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.intent_service import IntentService
from autopatch_j.core.models import (
    AuditAttemptOutcome,
    AuditFindingItem,
    CodeScope,
    CodeScopeKind,
    ConversationRoute,
    IntentType,
    PatchReviewItem,
)
from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
from autopatch_j.core.scan_service import ScanService
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.validator_service import SemanticValidator
from autopatch_j.core.workflow_service import WorkflowService
from autopatch_j.scanners import DEFAULT_SCANNER_NAME, get_scanner
from autopatch_j.scanners.base import ScanResult

DSML_MARKER_PATTERN = re.compile(r"<[^>\n]*DSML[^>\n]*>", re.IGNORECASE)


class AutoPatchCLI:
    """
    AutoPatch-J CLI 控制器
    职责：保留交互、补全与渲染，把任务编排交给核心服务。
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.renderer = CliRenderer()

        self.artifacts: ArtifactManager | None = None
        self.indexer: IndexService | None = None
        self.patch_engine: PatchEngine | None = None
        self.fetcher: CodeFetcher | None = None
        self.intent_service: IntentService | None = None
        self.continuity_judge_service: ContinuityJudgeService | None = None
        self.audit_backlog_service: AuditBacklogService | None = None
        self.scope_service: ScopeService | None = None
        self.scan_service: ScanService | None = None
        self.workflow_service: WorkflowService | None = None
        self.agent: AutoPatchAgent | None = None

        self.prompt_session: PromptSession[str] | None = None
        if self.repo_root:
            self._init_services(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.renderer.print(f"\n[bold {DECISION_STYLE}]收到中断信号，正在退出...[/]")
        sys.exit(0)

    def _create_prompt_session(self) -> PromptSession[str]:
        key_bindings = KeyBindings()

        @key_bindings.add("enter")
        def _(event: Any) -> None:
            buffer = event.app.current_buffer
            if buffer.complete_state:
                changed = self._accept_completion(buffer)
                if changed:
                    return
            buffer.validate_and_handle()

        @key_bindings.add("tab")
        def _(event: Any) -> None:
            buffer = event.app.current_buffer
            self._accept_completion(buffer)

        custom_style = Style.from_dict(
            {
                "completion-menu.completion": "bg:#333333 #ffffff",
                "completion-menu.completion.current": "bg:#007acc #ffffff bold",
                "completion-menu.meta.completion": "bg:#222222 #888888",
                "completion-menu.meta.completion.current": "bg:#007acc #ffffff",
            }
        )

        history = None
        if self.repo_root:
            history = FileHistory(str(get_project_state_dir(self.repo_root) / "history.txt"))

        session = PromptSession(
            completer=AutoPatchCompleter(self.indexer.search if self.indexer else lambda _: []),
            key_bindings=key_bindings,
            style=custom_style,
            complete_while_typing=True,
            history=history,
        )

        def auto_select_first(buffer: Any) -> None:
            self._select_first_completion(buffer)

        session.default_buffer.on_completions_changed += auto_select_first
        return session

    def _ensure_prompt_session(self) -> bool:
        if self.prompt_session is not None:
            return True
        try:
            self.prompt_session = self._create_prompt_session()
            return True
        except Exception as exc:
            self.renderer.print_error(f"CLI 输入环境初始化失败: {exc}")
            return False

    def _pick_active_completion(self, buffer: Any) -> Any:
        state = getattr(buffer, "complete_state", None)
        if not state:
            return None
        completions = getattr(state, "completions", None) or []
        index = getattr(state, "complete_index", None)
        if isinstance(index, int) and 0 <= index < len(completions):
            return completions[index]
        return completions[0] if completions else None

    def _accept_completion(self, buffer: Any) -> bool:
        append_space = self._should_append_space_after_completion(buffer)
        completion = self._pick_active_completion(buffer)
        if completion is None:
            buffer.start_completion(select_first=False)
            append_space = self._should_append_space_after_completion(buffer)
            completion = self._pick_active_completion(buffer)
        if completion is None:
            return False
        before_text = getattr(buffer, "text", None)
        before_cursor = getattr(buffer.document, "cursor_position", None)
        buffer.apply_completion(completion)
        changed = (
            getattr(buffer, "text", None) != before_text
            or getattr(buffer.document, "cursor_position", None) != before_cursor
        )
        current_char = getattr(buffer.document, "current_char", "")
        if append_space and (current_char is None or not str(current_char).isspace()):
            buffer.insert_text(" ")
            changed = True
        return changed

    def _select_first_completion(self, buffer: Any) -> bool:
        state = getattr(buffer, "complete_state", None)
        if not state:
            return False
        completions = getattr(state, "completions", None) or []
        index = getattr(state, "complete_index", None)
        if not completions or (isinstance(index, int) and 0 <= index < len(completions)):
            return False
        state.go_to_index(0)
        get_app().invalidate()
        return True

    def _should_append_space_after_completion(self, buffer: Any) -> bool:
        state = getattr(buffer, "complete_state", None)
        if not state:
            return False
        original_document = getattr(state, "original_document", None)
        if not original_document:
            return False
        return bool(re.search(r"(^|\s)@[\w\.]*$", original_document.text_before_cursor))

    def _init_services(self, repo_root: Path) -> None:
        self.artifacts = ArtifactManager(repo_root)
        self.indexer = IndexService(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
        self.patch_engine = PatchEngine(repo_root)
        self.fetcher = CodeFetcher(repo_root)
        self.intent_service = IntentService()
        self.audit_backlog_service = AuditBacklogService()
        shared_llm = build_default_llm_client()
        self.continuity_judge_service = ContinuityJudgeService(llm=shared_llm)
        self.scope_service = ScopeService(repo_root, self.indexer, ignored_dirs=GlobalConfig.ignored_dirs)
        self.scan_service = ScanService(repo_root, self.artifacts)
        self.workflow_service = WorkflowService(self.artifacts)
        self.agent = AutoPatchAgent(
            repo_root=repo_root,
            artifacts=self.artifacts,
            indexer=self.indexer,
            patch_engine=self.patch_engine,
            fetcher=self.fetcher,
            llm=shared_llm,
        )

    def run(self) -> int:
        if not self._ensure_prompt_session():
            return 1

        self.renderer.print_panel(
            "AutoPatch-J: Java 安全与正确性修复智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="AutoPatch-J",
            style=SYSTEM_STYLE,
        )
        if not self.repo_root:
            self.renderer.print_info("未检测到 Java 项目，请进入项目目录后执行 /init")
        else:
            self.renderer.print(
                f"[{MUTED_STYLE}]当前项目:[/] [bold {SYSTEM_STYLE}]{self.repo_root}[/]"
            )

        while True:
            try:
                workspace = self.workflow_service.fetch_workspace() if self.workflow_service else None
                current_item = workspace.fetch_current_patch_item() if workspace else None
                pending_draft = current_item.draft.fetch_patch_draft() if current_item else None
                current_idx, total_count = workspace.fetch_review_progress() if workspace else (0, 0)
                prompt_prefix = "autopatch-j"

                if pending_draft:
                    self.renderer.print_diff(pending_draft.diff, title=f" 预览: {pending_draft.file_path} ")
                    self.renderer.print_action_panel(
                        file_path=pending_draft.file_path,
                        diff=pending_draft.diff,
                        validation=pending_draft.validation.status,
                        rationale=pending_draft.rationale or "无说明",
                        current_idx=current_idx,
                        total_count=total_count,
                    )
                    prompt_prefix = f"<style fg='{DECISION_STYLE}' font_weight='bold'>PENDING</style> autopatch-j"

                user_input = self.prompt_session.prompt(HTML(f"{prompt_prefix}> ")).strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    self.handle_command(user_input)
                    continue

                if current_item is not None:
                    self._handle_review_input(user_input, current_item)
                else:
                    self.handle_chat(user_input)

            except (EOFError, KeyboardInterrupt):
                break
            except Exception as exc:
                error_message = str(exc)
                if "401" in error_message or "AuthenticationError" in error_message:
                    self.renderer.print_error("LLM 认证失败 (401)，请检查 LLM_API_KEY。")
                elif "403" in error_message or "AccessDenied" in error_message:
                    self.renderer.print_error("LLM 模型无访问权限 (403)，请检查模型权限或账户余额。")
                elif "404" in error_message or "NotFoundError" in error_message:
                    self.renderer.print_error("LLM 接口未找到 (404)，请检查 LLM_BASE_URL 或 LLM_MODEL。")
                else:
                    self.renderer.print_error(f"指令执行异常: {error_message}")

        return 0

    def _handle_review_input(self, user_input: str, current_item: PatchReviewItem) -> None:
        current_draft = current_item.draft.fetch_patch_draft()
        assert self.workflow_service is not None

        if user_input.lower() == "apply":
            self.handle_apply(current_draft)
            self.workflow_service.persist_applied_current_patch()
            if not self.workflow_service.verify_has_pending_patch():
                self.renderer.print_info("补丁队列已清空")
            return

        if user_input.lower() == "discard":
            self.handle_discard()
            self.workflow_service.persist_discarded_current_patch()
            if not self.workflow_service.verify_has_pending_patch():
                self.renderer.print_info("补丁队列已清空")
            return

        self.handle_chat(user_input)

    def handle_chat(self, text: str) -> None:
        if not all(
            [
                self.agent,
                self.intent_service,
                self.continuity_judge_service,
                self.scope_service,
                self.scan_service,
                self.workflow_service,
            ]
        ):
            self.renderer.print_error("系统未初始化，请先执行 /init")
            return

        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.renderer.print_info("请继续输入代码指令")
            return

        has_pending_review = self.workflow_service.verify_has_pending_patch()
        requested_scope = self.scope_service.fetch_scope(text, default_to_project=False)
        current_item = self.workflow_service.fetch_current_patch_item() if has_pending_review else None
        current_workspace = self.workflow_service.fetch_workspace() if has_pending_review else None
        route = self.continuity_judge_service.fetch_route(
            user_text=text,
            has_pending_review=has_pending_review,
            requested_scope=requested_scope,
            current_patch_file=current_item.file_path if current_item else None,
            current_scope=current_workspace.scope if current_workspace else None,
        )

        if route is ConversationRoute.COMMAND:
            self.handle_command(text)
            return

        if route is ConversationRoute.NEW_TASK:
            self.agent.reset_history()
            if has_pending_review:
                self.workflow_service.clear_workspace()
                self.renderer.print_info("已切换到新任务")
            intent = self.intent_service.fetch_intent(text, has_pending_review=False)
        else:
            intent = self.intent_service.fetch_intent(text, has_pending_review=True)

        if intent is IntentType.CODE_AUDIT:
            self._handle_code_audit(text)
            return
        if intent is IntentType.CODE_EXPLAIN:
            self._handle_code_explain(text)
            return
        if intent is IntentType.PATCH_EXPLAIN:
            self._handle_patch_explain(text)
            return
        if intent is IntentType.PATCH_REVISE:
            self._handle_patch_revise(text)
            return
        self._handle_general_chat(text)

    def _handle_code_audit(self, text: str) -> None:
        assert self.scope_service is not None
        assert self.scan_service is not None
        assert self.workflow_service is not None
        assert self.agent is not None
        assert self.audit_backlog_service is not None

        scope = self.scope_service.fetch_scope(text, default_to_project=True)
        if scope is None:
            self.renderer.print_error("未解析到可检查范围")
            return

        self.agent.set_focus_paths(scope.focus_files if scope.is_locked else [])
        try:
            self.renderer.print_tool_start("scan_project", caller="AGENT")
            scan_id, scan_result = self.scan_service.fetch_scan_snapshot(scope)
        except RuntimeError as exc:
            self.renderer.print_error(str(exc))
            return

        self.workflow_service.persist_review_workspace(scope=scope, latest_scan_id=scan_id, patch_items=[])
        backlog = self.audit_backlog_service.fetch_backlog(scan_result)
        if not backlog:
            self._run_agent_request(
                prompt=text,
                agent_call=self.agent.perform_code_audit,
                scope_paths=self._describe_scope_paths(scope),
                render_no_issue_panel=True,
            )
            if not self.workflow_service.verify_has_pending_patch():
                self.workflow_service.clear_workspace()
            return

        while self.audit_backlog_service.verify_has_pending_finding(backlog):
            current_finding = self.audit_backlog_service.fetch_current_finding(backlog)
            if current_finding is None:
                break

            self.agent.reset_history()
            prompt = self._build_code_audit_prompt(
                text=text,
                current_finding=current_finding,
                force_reread=False,
            )
            new_messages = self._run_agent_request(
                prompt=prompt,
                agent_call=self.agent.perform_code_audit,
            ) or []
            decision = self.audit_backlog_service.fetch_attempt_decision(current_finding, new_messages)
            if decision.outcome is AuditAttemptOutcome.PATCH_READY:
                self.audit_backlog_service.persist_mark_patch_ready(backlog, current_finding.finding_id)
                continue

            if (
                decision.outcome is AuditAttemptOutcome.RETRYABLE_ERROR
                and self.audit_backlog_service.verify_can_retry(current_finding)
            ):
                self.audit_backlog_service.persist_mark_retry(
                    backlog=backlog,
                    finding_id=current_finding.finding_id,
                    error_code=decision.error_code,
                    error_message=decision.error_message,
                )
                self.agent.reset_history()
                retry_prompt = self._build_code_audit_prompt(
                    text=text,
                    current_finding=current_finding,
                    force_reread=True,
                )
                retry_messages = self._run_agent_request(
                    prompt=retry_prompt,
                    agent_call=self.agent.perform_code_audit,
                ) or []
                retry_decision = self.audit_backlog_service.fetch_attempt_decision(current_finding, retry_messages)
                if retry_decision.outcome is AuditAttemptOutcome.PATCH_READY:
                    self.audit_backlog_service.persist_mark_patch_ready(backlog, current_finding.finding_id)
                else:
                    self.audit_backlog_service.persist_mark_failed(
                        backlog=backlog,
                        finding_id=current_finding.finding_id,
                        error_code=retry_decision.error_code,
                        error_message=retry_decision.error_message,
                    )
                continue

            self.audit_backlog_service.persist_mark_failed(
                backlog=backlog,
                finding_id=current_finding.finding_id,
                error_code=decision.error_code,
                error_message=decision.error_message,
            )

        if not self.workflow_service.verify_has_pending_patch():
            self.workflow_service.clear_workspace()

    def _handle_code_explain(self, text: str) -> None:
        assert self.scope_service is not None
        assert self.agent is not None

        scope = self.scope_service.fetch_scope(text, default_to_project=False)
        compact_observation = not self._should_show_full_tool_output(text)
        if scope is not None and scope.is_locked:
            self.agent.set_focus_paths(scope.focus_files)
            prompt = self._build_code_explain_prompt(text=text, scope=scope)
            allow_symbol_search = scope.kind is not CodeScopeKind.SINGLE_FILE
            self.agent.set_code_explain_symbol_search_enabled(allow_symbol_search)
            self._run_agent_request(
                prompt=prompt,
                agent_call=self.agent.perform_code_explain,
                compact_observation=compact_observation,
            )
            return

        self.agent.set_focus_paths([])
        self.agent.set_code_explain_symbol_search_enabled(True)
        self._run_agent_request(
            prompt=text,
            agent_call=self.agent.perform_general_chat,
            compact_observation=compact_observation,
        )

    def _handle_general_chat(self, text: str) -> None:
        assert self.agent is not None
        self.agent.set_focus_paths([])
        self._run_agent_request(
            prompt=text,
            agent_call=self.agent.perform_general_chat,
            compact_observation=not self._should_show_full_tool_output(text),
        )

    def _handle_patch_explain(self, text: str) -> None:
        assert self.workflow_service is not None
        assert self.agent is not None

        current_item = self.workflow_service.fetch_current_patch_item()
        if current_item is None:
            self.renderer.print_error("当前没有待确认补丁")
            return

        focus_paths = self._fetch_review_scope_paths(current_item)
        self.agent.set_focus_paths(focus_paths)
        prompt = self._build_patch_explain_prompt(current_item=current_item, user_text=text)
        self._run_agent_request(
            prompt=prompt,
            agent_call=self.agent.perform_patch_explain,
        )

    def _handle_patch_revise(self, text: str) -> None:
        assert self.workflow_service is not None
        assert self.agent is not None

        current_item = self.workflow_service.fetch_current_patch_item()
        if current_item is None:
            self.renderer.print_error("当前没有待确认补丁")
            return

        remaining_items = self.workflow_service.fetch_remaining_patch_items()
        prompt = self._build_patch_revise_prompt(
            current_item=current_item,
            remaining_items=remaining_items,
            user_text=text,
        )
        self.agent.set_focus_paths(self._fetch_review_scope_paths(current_item))
        self.workflow_service.persist_replaced_remaining_patch_items([])
        self._run_agent_request(
            prompt=prompt,
            agent_call=self.agent.perform_patch_revise,
        )
        if not self.workflow_service.verify_has_pending_patch():
            self.renderer.print_info("补丁队列已清空")

    def _run_agent_request(
        self,
        prompt: str,
        agent_call: Callable[..., str],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
        compact_observation: bool = False,
    ) -> list[dict[str, Any]]:
        assert self.agent is not None
        assert self.workflow_service is not None

        self.renderer.print()
        stream_state = {"in_reasoning": False, "answer_after_reasoning": False}
        buffered_answer_parts: list[str] = []
        start_index = len(self.agent.messages)
        current_tool_name: str | None = None

        def on_token(token: str) -> None:
            if stream_state["in_reasoning"]:
                stream_state["answer_after_reasoning"] = True
                stream_state["in_reasoning"] = False
            buffered_answer_parts.append(token)

        def on_reasoning(token: str) -> None:
            stream_state["in_reasoning"] = True
            self.renderer.print_reasoning(token, end="")

        def on_tool_start(tool_name: str) -> None:
            nonlocal current_tool_name
            current_tool_name = tool_name
            self.renderer.print_tool_start(tool_name, caller="LLM")

        def on_observation(message: str) -> None:
            if compact_observation:
                self.renderer.print_info(self._summarize_observation(current_tool_name, message))
                return
            self.renderer.print_observation(message)

        final_answer = agent_call(
            prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )
        new_messages = list(self.agent.messages[start_index:])

        has_pending_patches = self.workflow_service.verify_has_pending_patch()
        if has_pending_patches:
            self.renderer.print()
            return new_messages

        if render_no_issue_panel:
            if buffered_answer_parts or final_answer:
                self.renderer.print("\n")
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            self.renderer.print()
            return new_messages

        buffered_answer = self._sanitize_assistant_output("".join(buffered_answer_parts))
        if buffered_answer:
            if stream_state["answer_after_reasoning"]:
                self.renderer.print("\n\n")
            self.renderer.print(buffered_answer, end="")
        else:
            sanitized_final_answer = self._sanitize_assistant_output(final_answer or "")
            if sanitized_final_answer:
                self.renderer.print(f"\n{sanitized_final_answer}")
        self.renderer.print()
        return new_messages

    def handle_command(self, raw_cmd: str) -> None:
        parts = raw_cmd.split()
        cmd = parts[0].lower()

        if cmd == "/init":
            self.handle_init()
        elif cmd == "/status":
            self.handle_status()
        elif cmd == "/reindex":
            self.handle_reindex()
        elif cmd == "/scanner":
            self.handle_scanners()
        elif cmd == "/help":
            self.handle_help()
        elif cmd == "/quit":
            sys.exit(0)
        else:
            self.renderer.print_error(f"未知命令：{cmd}")

    def _sanitize_assistant_output(self, text: str) -> str:
        match = DSML_MARKER_PATTERN.search(text)
        return text[:match.start()].rstrip() if match else text

    def _should_show_full_tool_output(self, text: str) -> bool:
        hints = (
            "展示代码",
            "显示代码",
            "贴出代码",
            "展示源码",
            "显示源码",
            "详细过程",
            "完整过程",
            "工具结果",
            "原始输出",
            "完整输出",
            "逐步过程",
        )
        compact = re.sub(r"\s+", "", text)
        return any(hint in compact for hint in hints)

    def _summarize_observation(self, tool_name: str | None, message: str) -> str:
        if tool_name == "read_source_code":
            match = re.search(r"\[[^:\]]+:\s*([^\]]+)\]", message)
            if match:
                return f"已读取: {match.group(1)}"
            return "已读取源码"

        if tool_name == "search_symbols":
            match = re.search(r"与 '([^']+)' 相关", message)
            if match:
                return f"已定位符号: {match.group(1)}"
            return "已定位相关符号"

        if tool_name == "get_finding_detail":
            match = re.search(r"\((F\d+)\)", message)
            if match:
                return f"已获取发现详情: {match.group(1)}"
            return "已获取发现详情"

        first_line = message.strip().splitlines()[0] if message.strip() else ""
        if first_line:
            return first_line
        if tool_name:
            return f"已完成工具: {tool_name}"
        return "已更新工具结果"

    def _build_code_audit_summary_prompt_legacy(self, text: str, scan_result: ScanResult) -> str:
        if not scan_result.findings:
            return text

        summary_lines = [
            "系统已完成本地静态扫描，请优先围绕以下扫描结果继续处理：",
            "扫描摘要：",
        ]
        for index, finding in enumerate(scan_result.findings, start=1):
            summary_lines.append(
                f"- F{index}: {finding.path}:{finding.start_line} ({finding.check_id})"
            )
        summary_lines.extend(
            [
                "",
                "执行要求：",
                "1. 优先根据 F 编号调用 get_finding_detail 获取详情。",
                "2. 仅在需要确认最新源码时调用 read_source_code。",
                "3. 如果能够形成补丁，直接 propose_patch，不要输出长篇分析。",
                "",
                f"用户原始请求：{text}",
            ]
        )
        return "\n".join(summary_lines)

    def _should_render_local_no_issue_summary(self, new_messages: list[dict[str, Any]]) -> bool:
        saw_zero_scan = False
        for message in new_messages:
            if message.get("role") != "tool":
                continue
            if message.get("name") == "propose_patch":
                return False
            if message.get("name") == "scan_project":
                content = str(message.get("content", ""))
                if "共发现 0 个问题" in content or "未发现任何安全或正确性问题" in content:
                    saw_zero_scan = True
        return saw_zero_scan

    def _build_patch_explain_prompt(self, current_item: PatchReviewItem, user_text: str) -> str:
        draft = current_item.draft
        return (
            f"当前待审核补丁文件: {current_item.file_path}\n"
            f"补丁意图: {draft.rationale or '无说明'}\n"
            f"补丁差异:\n{draft.diff}\n\n"
            f"用户问题:\n{user_text}"
        )

    def _build_patch_revise_prompt(
        self,
        current_item: PatchReviewItem,
        remaining_items: list[PatchReviewItem],
        user_text: str,
    ) -> str:
        draft = current_item.draft
        remaining_files = "\n".join(f"- {item.file_path}" for item in remaining_items)
        return (
            f"当前待重写补丁文件: {current_item.file_path}\n"
            f"当前补丁意图: {draft.rationale or '无说明'}\n"
            f"当前补丁差异:\n{draft.diff}\n\n"
            "以下补丁尾部已失效，需要基于用户反馈整体重建:\n"
            f"{remaining_files}\n\n"
            f"用户反馈:\n{user_text}\n"
            "请基于最新意见重新生成 remaining_patch_items。"
        )

    def _build_patch_feedback_prompt(
        self,
        pending_file: str,
        user_feedback: str,
        discarded_followups: list[Any],
    ) -> str:
        if not discarded_followups:
            return (
                f"[系统反馈] 针对当前挂起的对 {pending_file} 的补丁，用户提供了修改意见：\n"
                f"{user_feedback}\n"
                "请仅针对该文件重新调用 propose_patch 生成修正后的补丁。"
            )

        followup_files: list[str] = []
        for draft in discarded_followups:
            file_path = getattr(draft, "file_path", "")
            if file_path and file_path not in followup_files:
                followup_files.append(file_path)
        followup_text = "\n".join(f"- {path}" for path in followup_files)
        return (
            f"[系统反馈] 针对当前挂起的对 {pending_file} 的补丁，用户提供了修改意见：\n"
            f"{user_feedback}\n"
            "由于当前补丁发生变更，以下后续补丁已作废，需要基于最新方案重新规划：\n"
            f"{followup_text}\n"
            "请先为上述后续文件重新调用 propose_patch（如果仍值得修复），"
            f"最后再针对当前文件调用 propose_patch 生成修正后的补丁：{pending_file}\n"
            "当前文件的补丁必须最后提交，以便继续保留在队列顶部供用户审核。"
        )

    def _build_local_no_issue_summary(self) -> str:
        return "模型复核未发现需要修复的问题。"

    def _build_static_scan_summary(self) -> str:
        return "当前范围未发现安全或正确性问题。"

    def _build_code_explain_prompt(self, text: str, scope: CodeScope) -> str:
        if scope.kind is CodeScopeKind.SINGLE_FILE:
            return (
                f"当前解释范围仅限文件: {scope.focus_files[0]}\n"
                "请只基于当前文件可见内容解释代码功能，不要主动搜索、读取或推断焦点范围外的类型实现、调用方或配置来源。"
                "如果出现外部类型名，只能基于当前文件里的使用方式做保守说明。"
                "回答默认控制在 2 到 4 句；除非用户明确要求详细展开，否则不要输出分节报告。\n\n"
                f"用户问题:\n{text}"
            )

        joined_paths = "\n".join(f"- {path}" for path in scope.focus_files)
        return (
            "当前任务是代码讲解。你可以在当前焦点范围内使用 search_symbols 和 read_source_code 辅助解释，"
            "但不要越过当前 focus scope。回答默认控制在 1 段或 3 个要点以内；"
            "除非用户明确要求详细展开，否则不要输出长篇报告。\n"
            f"当前焦点范围:\n{joined_paths}\n\n"
            f"用户问题:\n{text}"
        )

    def _build_code_audit_prompt(
        self,
        text: str,
        current_finding: AuditFindingItem,
        force_reread: bool,
    ) -> str:
        lines = [
            "系统已完成本地静态扫描。你当前只允许处理一个 finding，不要切换到其他目标。",
            f"当前目标: {current_finding.finding_id}",
            f"文件位置: {current_finding.file_path}:{current_finding.start_line}",
            f"规则 ID: {current_finding.check_id}",
            f"问题描述: {current_finding.message}",
            f"代码证据:\n```java\n{current_finding.snippet}\n```",
            "",
            "执行要求：",
            f"1. 只处理 {current_finding.finding_id}，不要切换到其他 F 编号。",
            "2. 优先根据 F 编号调用 get_finding_detail 获取详情。",
            f"3. 如需漏洞详情，associated_finding_id 必须使用 {current_finding.finding_id}。",
            f"4. 如需最新源码，可读取 {current_finding.file_path}。",
            f"5. 如果形成补丁，propose_patch 时必须传 associated_finding_id={current_finding.finding_id}。",
            "6. 如果你判断当前目标不值得修复，只输出一句短结论，不要展开长篇分析。",
        ]
        if force_reread:
            lines.extend(
                [
                    "",
                    "上一次 propose_patch 因 old_string 不匹配失败。",
                    f"这一次你必须先 read_source_code({current_finding.file_path})，再重新 propose_patch。",
                ]
            )
        lines.extend(["", f"用户原始请求: {text}"])
        return "\n".join(lines)

    def _fetch_review_scope_paths(self, current_item: PatchReviewItem) -> list[str]:
        assert self.workflow_service is not None
        workspace = self.workflow_service.fetch_workspace()
        if workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        return [current_item.file_path]

    def _describe_scope_paths(self, scope: CodeScope) -> list[str]:
        if scope.kind.value == "project":
            return list(scope.focus_files)
        return list(scope.focus_files)

    def _describe_current_scope_paths(self) -> list[str]:
        workflow_service = getattr(self, "workflow_service", None)
        workspace = workflow_service.fetch_workspace() if workflow_service else None
        if workspace is not None and workspace.scope is not None and workspace.scope.focus_files:
            return list(workspace.scope.focus_files)
        scan_paths = self._collect_latest_scan_paths()
        if scan_paths:
            return scan_paths
        if self.agent and self.agent.focus_paths:
            return list(self.agent.focus_paths)
        return ["当前范围"]

    def _collect_latest_scan_paths(self) -> list[str]:
        if not self.artifacts:
            return []
        scan_files = sorted(self.artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return []
        latest = self.artifacts.fetch_scan_result(scan_files[0].stem)
        if latest is None:
            return []

        resolved: list[str] = []
        for target in latest.targets:
            normalized = str(target).replace("\\", "/")
            candidate = (self.repo_root / normalized).resolve()
            if candidate.is_dir():
                for java_file in sorted(candidate.rglob("*.java")):
                    rel_path = java_file.relative_to(self.repo_root).as_posix()
                    if rel_path not in resolved:
                        resolved.append(rel_path)
            else:
                if normalized not in resolved:
                    resolved.append(normalized)
        return resolved

    def handle_help(self) -> None:
        from rich.table import Table

        sys_table = Table(show_header=True, header_style=f"bold {SYSTEM_STYLE}", box=None)
        sys_table.add_column("系统命令", style=SYSTEM_STYLE, width=15)
        sys_table.add_column("功能描述")
        sys_table.add_row("/init", "初始化当前目录为 Java 项目并建立索引")
        sys_table.add_row("/status", "查看当前项目状态与索引统计")
        sys_table.add_row("/scanner", "查看扫描器状态")
        sys_table.add_row("/reindex", "强制重新扫描全项符号")
        sys_table.add_row("/help", "显示命令帮助")
        sys_table.add_row("/quit", "安全退出程序")

        act_table = Table(show_header=True, header_style=f"bold {DECISION_STYLE}", box=None)
        act_table.add_column("交互关键字", style=DECISION_STYLE, width=15)
        act_table.add_column("用法说明")
        act_table.add_row("@符号", "触发类/方法/路径的实时补全")
        act_table.add_row("apply", "应用当前补丁预览")
        act_table.add_row("discard", "丢弃当前补丁草案")

        self.renderer.print_panel("命令帮助", style=SYSTEM_STYLE)
        self.renderer.console.print(sys_table)
        self.renderer.print("\n[bold]交互说明[/bold]")
        self.renderer.console.print(act_table)

    def handle_scanners(self) -> None:
        from rich.table import Table

        from autopatch_j.scanners import ALL_SCANNERS

        table = Table(title="扫描器状态", show_header=True, header_style=f"bold {SYSTEM_STYLE}")
        table.add_column("名称", style=SYSTEM_STYLE, width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("功能简述")

        for scanner in ALL_SCANNERS:
            meta = scanner.get_meta(self.repo_root)
            status_text = (
                f"[green]● {meta.status}[/green]"
                if meta.is_implemented
                else f"[dim]● {meta.status}[/dim]"
            )
            table.add_row(meta.name, status_text, meta.version if meta.is_implemented else "-", meta.description)

        self.renderer.console.print(table)

    def handle_init(self) -> None:
        self.renderer.print_step("正在初始化 AutoPatch-J 环境...")
        self.repo_root = self.cwd
        self._init_services(self.repo_root)
        assert self.artifacts is not None
        self.artifacts.clear_pending_patch()

        from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime

        status, _ = install_managed_semgrep_runtime()
        self.renderer.print_step(f"扫描器运行时自检: {status}")

        assert self.indexer is not None
        stats = self.indexer.perform_rebuild()
        self.renderer.print_success(f"初始化完成，索引 {stats.get('total', 0)} 项")

    def handle_status(self) -> None:
        if not self.indexer or not self.workflow_service:
            self.renderer.print_error("系统未初始化，请先执行 /init")
            return

        from rich.table import Table

        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", style=SYSTEM_STYLE, width=15)
        table.add_column("Value")

        table.add_row("[bold]项目根目录[/]", str(self.repo_root))
        table.add_row("[bold]LLM 模型[/]", f"{GlobalConfig.llm_model} ([dim]{GlobalConfig.llm_base_url}[/])")

        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        scanner_meta = scanner.get_meta(self.repo_root) if scanner else None
        scanner_status = (
            f"[green]就绪 ({scanner_meta.version})[/]"
            if scanner_meta and scanner_meta.is_implemented and "就绪" in scanner_meta.status
            else "[red]未就绪[/]"
        )
        table.add_row("[bold]静态扫描器[/]", scanner_status)

        pending = self.workflow_service.fetch_current_patch_item()
        buffer_status = (
            f"[bold yellow]存在待确认补丁 ({pending.file_path})[/]"
            if pending
            else "[dim]空闲[/]"
        )
        table.add_row("[bold]补丁缓冲区[/]", buffer_status)

        stats = self.indexer.get_stats()
        stats_str = (
            f"文件:{stats.get('file', 0)} | 类:{stats.get('class', 0)} | "
            f"方法:{stats.get('method', 0)} (总计:{stats.get('total', 0)})"
        )
        table.add_row("[bold]符号索引[/]", stats_str)

        self.renderer.print_panel(table, title="[bold] 项目状态 [/]", style=SYSTEM_STYLE)

    def handle_reindex(self) -> None:
        if not self.indexer:
            return
        self.renderer.print_step("正在重新构建索引...")
        stats = self.indexer.perform_rebuild()
        self.renderer.print_success(f"索引刷新完成，累计 {stats.get('total', 0)} 项")

    def handle_apply(self, pending: PatchDraft) -> None:
        assert self.patch_engine is not None
        self.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        if not self.patch_engine.perform_apply(pending):
            self.renderer.print_error("应用失败。")
            return

        self.renderer.print_success("补丁已应用")
        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        if scanner:
            validator = SemanticValidator(self.repo_root, scanner)
            success, message = validator.perform_verification(pending)
            if success:
                self.renderer.print_success(message)
            else:
                self.renderer.print_error(message)

    def handle_discard(self) -> None:
        self.renderer.print_info("已丢弃当前草案")


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
