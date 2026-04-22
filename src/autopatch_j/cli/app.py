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
from autopatch_j.cli.completer import AutoPatchCompleter
from autopatch_j.cli.render import CliRenderer
from autopatch_j.config import GlobalConfig, discover_repo_root, get_project_state_dir
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.intent_service import IntentService
from autopatch_j.core.models import CodeScope, IntentType, PatchReviewItem
from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
from autopatch_j.core.scan_service import ScanService
from autopatch_j.core.scope_service import ScopeService
from autopatch_j.core.validator_service import SemanticValidator
from autopatch_j.core.workflow_service import WorkflowService
from autopatch_j.scanners import DEFAULT_SCANNER_NAME, get_scanner

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
        self.scope_service: ScopeService | None = None
        self.scan_service: ScanService | None = None
        self.workflow_service: WorkflowService | None = None
        self.agent: AutoPatchAgent | None = None

        self.prompt_session: PromptSession[str] | None = None
        if self.repo_root:
            self._init_services(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.renderer.print("\n[bold yellow]检测到中断信号，正在安全退出...[/bold yellow]")
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
        self.scope_service = ScopeService(repo_root, self.indexer, ignored_dirs=GlobalConfig.ignored_dirs)
        self.scan_service = ScanService(repo_root, self.artifacts)
        self.workflow_service = WorkflowService(self.artifacts)
        self.agent = AutoPatchAgent(
            repo_root=repo_root,
            artifacts=self.artifacts,
            indexer=self.indexer,
            patch_engine=self.patch_engine,
            fetcher=self.fetcher,
        )

    def run(self) -> int:
        if not self._ensure_prompt_session():
            return 1

        self.renderer.print_panel(
            "AutoPatch-J: Java 安全与正确性修复智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="欢迎使用",
            style="cyan",
        )
        if not self.repo_root:
            self.renderer.print_info("未检测到 Java 项目。请进入项目目录并执行 /init。")
        else:
            self.renderer.print(f"当前项目: [bold cyan]{self.repo_root}[/bold cyan]")

        while True:
            try:
                current_item = self.workflow_service.fetch_current_patch_item() if self.workflow_service else None
                pending_draft = current_item.draft.fetch_patch_draft() if current_item else None
                remaining_count = len(self.workflow_service.fetch_remaining_patch_items()) if current_item else 0
                prompt_prefix = "autopatch-j"

                if pending_draft:
                    self.renderer.print_diff(pending_draft.diff, title=f" 预览: {pending_draft.file_path} ")
                    self.renderer.print_action_panel(
                        file_path=pending_draft.file_path,
                        diff=pending_draft.diff,
                        validation=pending_draft.validation.status,
                        rationale=pending_draft.rationale or "无说明",
                        current_idx=1,
                        total_count=remaining_count,
                    )
                    prompt_prefix = "<style fg='yellow' font_weight='bold'>PENDING</style> autopatch-j"

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
                self.renderer.print_info("补丁队列已清空。")
            return

        if user_input.lower() == "discard":
            self.handle_discard()
            self.workflow_service.persist_discarded_current_patch()
            if not self.workflow_service.verify_has_pending_patch():
                self.renderer.print_info("补丁队列已清空。")
            return

        self.handle_chat(user_input)

    def handle_chat(self, text: str) -> None:
        if not all(
            [
                self.agent,
                self.intent_service,
                self.scope_service,
                self.scan_service,
                self.workflow_service,
            ]
        ):
            self.renderer.print_error("系统未就绪。请先执行 /init。")
            return

        stripped_instruction = re.sub(r"@([^\s@]+)", "", text).strip()
        if "@" in text and not stripped_instruction:
            self.renderer.print_info("请继续输入对这些代码的指令。")
            return

        has_pending_review = self.workflow_service.verify_has_pending_patch()
        intent = self.intent_service.fetch_intent(text, has_pending_review=has_pending_review)

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

        scope = self.scope_service.fetch_scope(text, default_to_project=True)
        if scope is None:
            self.renderer.print_error("未解析到可审计的代码范围。")
            return

        self.agent.set_focus_paths(scope.focus_files if scope.is_locked else [])
        try:
            scan_id, scan_result = self.scan_service.fetch_scan_snapshot(scope)
        except RuntimeError as exc:
            self.renderer.print_error(str(exc))
            return

        self.workflow_service.persist_review_workspace(scope=scope, latest_scan_id=scan_id, patch_items=[])
        zero_finding_scan = len(scan_result.findings) == 0
        self._run_agent_request(
            prompt=text,
            agent_call=self.agent.perform_code_audit,
            scope_paths=self._describe_scope_paths(scope),
            render_no_issue_panel=zero_finding_scan,
        )
        if zero_finding_scan and not self.workflow_service.verify_has_pending_patch():
            self.workflow_service.clear_workspace()

    def _handle_code_explain(self, text: str) -> None:
        assert self.scope_service is not None
        assert self.agent is not None

        scope = self.scope_service.fetch_scope(text, default_to_project=False)
        if scope is not None and scope.is_locked:
            self.agent.set_focus_paths(scope.focus_files)
            self._run_agent_request(
                prompt=text,
                agent_call=self.agent.perform_code_explain,
            )
            return

        self.agent.set_focus_paths([])
        self._run_agent_request(
            prompt=text,
            agent_call=self.agent.perform_general_chat,
        )

    def _handle_general_chat(self, text: str) -> None:
        assert self.agent is not None
        self.agent.set_focus_paths([])
        self._run_agent_request(
            prompt=text,
            agent_call=self.agent.perform_general_chat,
        )

    def _handle_patch_explain(self, text: str) -> None:
        assert self.workflow_service is not None
        assert self.agent is not None

        current_item = self.workflow_service.fetch_current_patch_item()
        if current_item is None:
            self.renderer.print_error("当前没有待审核补丁。")
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
            self.renderer.print_error("当前没有待审核补丁。")
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
            self.renderer.print_info("补丁队列已清空。")

    def _run_agent_request(
        self,
        prompt: str,
        agent_call: Callable[..., str],
        scope_paths: list[str] | None = None,
        render_no_issue_panel: bool = False,
    ) -> None:
        assert self.agent is not None
        assert self.workflow_service is not None

        self.renderer.print()
        stream_state = {"in_reasoning": False, "answer_after_reasoning": False}
        buffered_answer_parts: list[str] = []

        def on_token(token: str) -> None:
            if stream_state["in_reasoning"]:
                stream_state["answer_after_reasoning"] = True
                stream_state["in_reasoning"] = False
            buffered_answer_parts.append(token)

        def on_reasoning(token: str) -> None:
            stream_state["in_reasoning"] = True
            self.renderer.print(token, end="", style="dim italic")

        def on_tool_start(tool_name: str) -> None:
            self.renderer.print(f"\n[bold blue]正在执行工具: {tool_name}...[/bold blue]")

        def on_observation(message: str) -> None:
            self.renderer.print(f"\n[dim]{message}[/dim]\n")

        final_answer = agent_call(
            prompt,
            on_token=on_token,
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start,
        )

        has_pending_patches = self.workflow_service.verify_has_pending_patch()
        if has_pending_patches:
            self.renderer.print()
            return

        if render_no_issue_panel:
            if buffered_answer_parts or final_answer:
                self.renderer.print("\n")
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths or self._describe_current_scope_paths(),
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
            self.renderer.print()
            return

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
            self.renderer.print_error(f"未知命令: {cmd}")

    def _sanitize_assistant_output(self, text: str) -> str:
        match = DSML_MARKER_PATTERN.search(text)
        return text[:match.start()].rstrip() if match else text

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

        sys_table = Table(show_header=True, header_style="bold cyan", box=None)
        sys_table.add_column("系统命令", style="cyan", width=15)
        sys_table.add_column("功能描述")
        sys_table.add_row("/init", "初始化当前目录为 Java 项目并建立索引")
        sys_table.add_row("/status", "查看当前项目状态与索引统计")
        sys_table.add_row("/scanner", "查看扫描器蓝图与运行时状态")
        sys_table.add_row("/reindex", "强制重新扫描全项符号")
        sys_table.add_row("/help", "显示此指令看板")
        sys_table.add_row("/quit", "安全退出程序")

        act_table = Table(show_header=True, header_style="bold yellow", box=None)
        act_table.add_column("交互关键字", style="yellow", width=15)
        act_table.add_column("用法说明")
        act_table.add_row("@符号", "触发类/方法/路径的实时补全")
        act_table.add_row("apply", "应用当前补丁预览")
        act_table.add_row("discard", "丢弃当前补丁草案")

        self.renderer.print_panel("AutoPatch-J 指令中心", style="cyan")
        self.renderer.console.print(sys_table)
        self.renderer.print("\n[bold]交互指引[/bold]")
        self.renderer.console.print(act_table)

    def handle_scanners(self) -> None:
        from rich.table import Table

        from autopatch_j.scanners import ALL_SCANNERS

        table = Table(title="Java 静态扫描器看板", show_header=True, header_style="bold magenta")
        table.add_column("名称", style="cyan", width=12)
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
        self.renderer.print_success(f"初始化完成！索引项: {stats.get('total', 0)}")

    def handle_status(self) -> None:
        if not self.indexer or not self.workflow_service:
            self.renderer.print_error("系统未就绪。请先执行 /init。")
            return

        from rich.table import Table

        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", style="cyan", width=15)
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

        self.renderer.print_panel(table, title="[bold] AutoPatch-J 系统驾驶舱 [/]", style="blue")

    def handle_reindex(self) -> None:
        if not self.indexer:
            return
        self.renderer.print_step("正在重新构建索引...")
        stats = self.indexer.perform_rebuild()
        self.renderer.print_success(f"索引刷新完成 ({stats.get('total', 0)})")

    def handle_apply(self, pending: PatchDraft) -> None:
        assert self.patch_engine is not None
        self.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        if not self.patch_engine.perform_apply(pending):
            self.renderer.print_error("应用失败。")
            return

        self.renderer.print_success("物理应用成功！")
        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        if scanner:
            validator = SemanticValidator(self.repo_root, scanner)
            success, message = validator.perform_verification(pending)
            if success:
                self.renderer.print_success(message)
            else:
                self.renderer.print_error(message)

    def handle_discard(self) -> None:
        self.renderer.print_info("已丢弃当前补丁草案。")


def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
