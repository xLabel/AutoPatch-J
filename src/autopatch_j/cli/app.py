from __future__ import annotations

import sys
import re
import signal
import traceback
from pathlib import Path
from typing import Any
from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import get_app
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from autopatch_j.config import discover_repo_root, get_project_state_dir, GlobalConfig
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.cli.render import CliRenderer
from autopatch_j.cli.completer import AutoPatchCompleter

DSML_MARKER_PATTERN = re.compile(r"<\s*[｜|]\s*DSML\s*[｜|]")


class AutoPatchCLI:
    """
    AutoPatch-J 主控制台 (Application Layer)
    职责：整合所有核心服务，驱动人机交互循环。
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.renderer = CliRenderer()
        
        # 核心服务实例
        self.artifacts: ArtifactManager | None = None
        self.indexer: IndexService | None = None
        self.patch_engine: PatchEngine | None = None
        self.fetcher: CodeFetcher | None = None
        self.agent: AutoPatchAgent | None = None

        if self.repo_root:
            self._init_services(self.repo_root)

        # 注册信号处理器 (Ctrl+C)
        signal.signal(signal.SIGINT, self._handle_interrupt)

        # 智能按键绑定：实现“回车即确认首项”
        kb = KeyBindings()

        @kb.add('enter')
        def _(event):
            buffer = event.app.current_buffer
            if buffer.complete_state:
                changed = self._accept_completion(buffer)
                if changed:
                    return
            buffer.validate_and_handle()

        @kb.add('tab')
        def _(event):
            buffer = event.app.current_buffer
            self._accept_completion(buffer)

        # 定义高对比度视觉样式
        custom_style = Style.from_dict({
            'completion-menu.completion': 'bg:#333333 #ffffff',
            'completion-menu.completion.current': 'bg:#007acc #ffffff bold',
            'completion-menu.meta.completion': 'bg:#222222 #888888',
            'completion-menu.meta.completion.current': 'bg:#007acc #ffffff',
        })

        # 接入全能补全器
        self.prompt_session = PromptSession(
            completer=AutoPatchCompleter(self.indexer.search if self.indexer else lambda _: []),
            key_bindings=kb,
            style=custom_style,
            complete_while_typing=True,
            history=FileHistory(str(get_project_state_dir(self.repo_root) / "history.txt")) if self.repo_root else None
        )

        def auto_select_first(buffer: Any) -> None:
            self._select_first_completion(buffer)

        self.prompt_session.default_buffer.on_completions_changed += auto_select_first

    def _handle_interrupt(self, signum, frame):
        """处理 Ctrl+C 信号"""
        self.renderer.print("\n[bold yellow]检测到中断信号，正在安全退出...[/bold yellow]")
        sys.exit(0)

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
            getattr(buffer, "text", None) != before_text or
            getattr(buffer.document, "cursor_position", None) != before_cursor
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
        """初始化核心 Service"""
        self.artifacts = ArtifactManager(repo_root)
        self.indexer = IndexService(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
        self.patch_engine = PatchEngine(repo_root)
        self.fetcher = CodeFetcher(repo_root)
        
        # 将服务直接注入 Agent
        self.agent = AutoPatchAgent(
            repo_root=repo_root,
            artifacts=self.artifacts,
            indexer=self.indexer,
            patch_engine=self.patch_engine,
            fetcher=self.fetcher
        )

    def run(self) -> int:
        self.renderer.print_panel(
            "AutoPatch-J: Java 安全与正确性修复智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="欢迎使用",
            style="cyan"
        )
        
        if not self.repo_root:
            self.renderer.print_info("未检测到 Java 项目。请进入项目目录并执行 /init。")
        else:
            self.renderer.print(f"当前项目: [bold cyan]{self.repo_root}[/bold cyan]")

        while True:
            try:
                from prompt_toolkit import HTML
                # 门禁检查：是否存在待确认补丁
                queue = self.artifacts.fetch_pending_patches() if self.artifacts else []
                pending = queue[0] if queue else None
                prompt_prefix = "autopatch-j"

                if pending:
                    self.renderer.print_diff(pending.diff, title=f" 预览: {pending.file_path} ")
                    self.renderer.print_action_panel(
                        file_path=pending.file_path,
                        diff=pending.diff,
                        validation=pending.validation.status,
                        rationale=pending.rationale or "无说明",
                        current_idx=1,
                        total_count=len(queue)
                    )
                    prompt_prefix = "<style fg='yellow' font_weight='bold'>PENDING</style> autopatch-j"

                try:
                    user_input = self.prompt_session.prompt(HTML(f"{prompt_prefix}> ")).strip()
                except (EOFError, KeyboardInterrupt):
                    break 

                if not user_input:
                    continue

                if pending:
                    if user_input.lower() == "apply":
                        self.handle_apply(pending)
                        self.artifacts.pop_pending_patch()
                        queue_after = self.artifacts.fetch_pending_patches()
                        if not queue_after:
                            self.renderer.print_info("补丁队列已清空。")
                        continue
                    elif user_input.lower() == "discard":
                        self.handle_discard()
                        self.artifacts.pop_pending_patch()
                        queue_after = self.artifacts.fetch_pending_patches()
                        if not queue_after:
                            self.renderer.print_info("补丁队列已清空。")
                        continue
                    elif not user_input.startswith("/"):
                        discarded_followups = self.artifacts.discard_followup_patches()
                        self.artifacts.pop_pending_patch()
                        if discarded_followups:
                            self.renderer.print_info(f"已作废当前补丁后的 {len(discarded_followups)} 个后续补丁，避免基于旧上下文继续排队。")
                        self.handle_chat(
                            self._build_patch_feedback_prompt(
                                pending_file=pending.file_path,
                                user_feedback=user_input,
                                discarded_followups=discarded_followups,
                            ),
                            preserve_focus=True
                        )
                        continue

                if user_input.startswith("/"):
                    self.handle_command(user_input)
                else:
                    self.handle_chat(user_input)

            except Exception as e:
                # 工业级异常防御：拦截所有未处理异常
                error_msg = str(e)
                if "401" in error_msg or "AuthenticationError" in error_msg:
                    self.renderer.print_error("LLM 认证失败 (401)：请检查您的 LLM_API_KEY 是否正确配置。")
                elif "403" in error_msg or "AccessDenied" in error_msg:
                    self.renderer.print_error("LLM 模型无访问权限 (403)：请检查您是否已在云平台开通该模型 (如 deepseek-v3) 的调用权限或账户余额是否充足。")
                elif "404" in error_msg or "NotFoundError" in error_msg:
                    self.renderer.print_error("LLM 接口未找到 (404)：请检查您的 LLM_BASE_URL 或 LLM_MODEL 配置是否正确。")
                else:
                    self.renderer.print_error(f"指令执行异常: {error_msg}")
            
        return 0

    def handle_chat(self, text: str, preserve_focus: bool = False) -> None:
        """处理自然语言对话及上下文注入"""
        if not self.agent or not self.indexer or not self.fetcher:
            self.renderer.print_error("系统未就绪。请先执行 /init。")
            return

        # 1. 提取上下文提及 (@mention)
        mentions = re.findall(r'@([\w\.]+)', text)
        instruction = re.sub(r'@[\w\.]+', '', text).strip()

        extra_context = ""
        resolved_count = 0
        failed_mentions = []
        focus_paths: list[str] = []

        for m in mentions:
            entries = self.indexer.search(m, limit=1)
            if entries:
                entry = entries[0]
                code = self.fetcher.fetch_entry(entry)
                if not code.startswith("错误"):
                    if entry.kind != "dir":
                        normalized = entry.path.replace("\\", "/")
                        if normalized not in focus_paths:
                            focus_paths.append(normalized)
                    extra_context += (
                        f"\n--- 引用代码片段 ({entry.kind}: {entry.name}) ---\n"
                        f"相对路径: {entry.path}\n"
                        f"{code}\n"
                    )
                    resolved_count += 1
                else:
                    failed_mentions.append(m)
            else:
                failed_mentions.append(m)
        
        # 2. 智能反馈与拦截
        if failed_mentions:
            for fm in failed_mentions:
                self.renderer.print_error(f"找不到代码符号或文件: @{fm}")

        if not instruction:
            if resolved_count > 0:
                self.renderer.print_info(f"已加载 {resolved_count} 处代码上下文。请接着输入您的指令。")
            elif mentions:
                pass
            else:
                self.renderer.print_info("请输入您的修复意图或使用 @ 符号引用代码。")
            return

        # 3. 准备调用 Agent
        final_prompt = text
        if extra_context:
            self.renderer.print_step(f"已自动注入 {resolved_count} 处代码上下文...")
            focus_hint = ""
            if focus_paths:
                joined = ", ".join(focus_paths)
                focus_hint = (
                    "--- 焦点约束 ---\n"
                    f"本轮唯一允许处理的文件: {joined}\n"
                    "禁止扫描、读取或修复其它文件。\n"
                )
            final_prompt = f"{focus_hint}{extra_context}\n--- 用户请求 ---\n{instruction}"

        if mentions:
            self.agent.set_focus_paths(focus_paths)
        elif not preserve_focus:
            self.agent.set_focus_paths([])

        self.renderer.print()
        pre_message_count = len(self.agent.messages)

        # 定义流式回调
        stream_state = {"in_reasoning": False, "answer_after_reasoning": False}
        buffered_answer_parts: list[str] = []

        def on_token(token: str) -> None:
            if stream_state["in_reasoning"]:
                stream_state["answer_after_reasoning"] = True
                stream_state["in_reasoning"] = False
            buffered_answer_parts.append(token)

        def on_reasoning(token: str) -> None:
            stream_state["in_reasoning"] = True
            # 思考链以淡灰色斜体打印
            self.renderer.print(token, end="", style="dim italic")

        def on_tool_start(tool_name: str) -> None:
            self.renderer.print(f"\n[bold blue]正在执行工具: {tool_name}...[/bold blue]")

        def on_observation(message: str) -> None:
            self.renderer.print(f"\n[dim]{message}[/dim]\n")

        # 4. 执行 Agent 循环并获取最终总结
        final_answer = self.agent.chat(
            final_prompt, 
            on_token=on_token, 
            on_reasoning=on_reasoning,
            on_observation=on_observation,
            on_tool_start=on_tool_start
        )
        new_messages = self.agent.messages[pre_message_count:]
        has_pending_patches = bool(self.artifacts.fetch_pending_patches()) if self.artifacts else False
        if has_pending_patches:
            pass
        elif self._should_render_local_no_issue_summary(new_messages):
            if buffered_answer_parts or final_answer:
                self.renderer.print("\n")
            scope_paths = self._describe_current_scope_paths()
            self.renderer.print_no_issue_panel(
                scope_paths=scope_paths,
                scanner_summary=self._build_static_scan_summary(),
                llm_summary=self._build_local_no_issue_summary(),
            )
        else:
            buffered_answer = self._sanitize_assistant_output("".join(buffered_answer_parts))
            if buffered_answer:
                if stream_state["answer_after_reasoning"]:
                    self.renderer.print("\n\n")
                self.renderer.print(buffered_answer, end="")
            else:
                sanitized_final_answer = self._sanitize_assistant_output(final_answer)
                if sanitized_final_answer and sanitized_final_answer not in self.agent.messages[-1].get('content', ''):
                    self.renderer.print(f"\n{sanitized_final_answer}")
        
        self.renderer.print()

    def handle_command(self, raw_cmd: str) -> None:
        parts = raw_cmd.split()
        cmd = parts[0].lower()

        if cmd == "/init": self.handle_init()
        elif cmd == "/status": self.handle_status()
        elif cmd == "/reindex": self.handle_reindex()
        elif cmd == "/scanner": self.handle_scanners()
        elif cmd == "/help": self.handle_help()
        elif cmd == "/quit": sys.exit(0)
        else: self.renderer.print_error(f"未知命令: {cmd}")

    def _should_render_local_no_issue_summary(self, new_messages: list[dict[str, Any]]) -> bool:
        saw_zero_scan = False
        for msg in new_messages:
            if msg.get("role") != "tool":
                continue
            if msg.get("name") == "propose_patch":
                return False
            if msg.get("name") == "scan_project":
                content = str(msg.get("content", ""))
                if "共发现 0 个问题" in content or "未发现任何安全或正确性问题" in content:
                    saw_zero_scan = True
        return saw_zero_scan

    def _sanitize_assistant_output(self, text: str) -> str:
        match = DSML_MARKER_PATTERN.search(text)
        return text[:match.start()].rstrip() if match else text

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

        followup_files = []
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
            "请先为上述后续文件重新调用 propose_patch（如果仍值得修复），最后再针对当前文件调用 propose_patch "
            f"生成修正后的补丁：{pending_file}\n"
            "当前文件的补丁必须最后提交，以便继续保留在队列顶部供用户审核。"
        )

    def _build_local_no_issue_summary(self) -> str:
        return "模型复核未发现需要修复的问题。"

    def _build_static_scan_summary(self) -> str:
        return "当前范围未发现安全或正确性问题。"

    def _describe_current_scope_paths(self) -> list[str]:
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
        if not latest:
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
        """展示精美的指令看板"""
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
        act_table.add_row("apply", "物理应用当前补丁预览")
        act_table.add_row("discard", "丢弃当前补丁草案")

        self.renderer.print_panel("AutoPatch-J 指令中心", style="cyan")
        self.renderer.console.print(sys_table)
        self.renderer.print("\n[bold]交互指引[/bold]")
        self.renderer.console.print(act_table)

    def handle_scanners(self) -> None:
        from autopatch_j.scanners import ALL_SCANNERS
        from rich.table import Table

        table = Table(title="Java 静态扫描器看板", show_header=True, header_style="bold magenta")
        table.add_column("名称", style="cyan", width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("功能简述")

        for scanner in ALL_SCANNERS:
            meta = scanner.get_meta(self.repo_root)
            status_text = f"[green]● {meta.status}[/green]" if meta.is_implemented else f"[dim]○ {meta.status}[/dim]"
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
        """展示全方位的系统驾驶舱看板"""
        if not self.indexer or not self.artifacts:
            self.renderer.print_error("系统未就绪。请先执行 /init。")
            return

        from rich.table import Table
        from autopatch_j.config import GlobalConfig
        from autopatch_j.scanners import get_scanner, DEFAULT_SCANNER_NAME

        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", style="cyan", width=15)
        table.add_column("Value")

        table.add_row("[bold]项目根目录[/]", str(self.repo_root))
        base_url = GlobalConfig.llm_base_url
        table.add_row("[bold]LLM 模型[/]", f"{GlobalConfig.llm_model} ([dim]{base_url}[/])")
        
        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        scanner_meta = scanner.get_meta(self.repo_root) if scanner else None
        scanner_status = f"[green]就绪 ({scanner_meta.version})[/]" if scanner_meta and scanner_meta.is_implemented and "就绪" in scanner_meta.status else "[red]未就绪[/]"
        table.add_row("[bold]静态扫描器[/]", scanner_status)

        pending = self.artifacts.fetch_pending_patch()
        buffer_status = f"[bold yellow]存在待确认补丁 ({pending.file_path})[/]" if pending else "[dim]空闲[/]"
        table.add_row("[bold]补丁缓冲区[/]", buffer_status)

        stats = self.indexer.get_stats()
        stats_str = f"文件:{stats.get('file',0)} | 类:{stats.get('class',0)} | 方法:{stats.get('method',0)} (总计:{stats.get('total',0)})"
        table.add_row("[bold]符号索引[/]", stats_str)

        self.renderer.print_panel(table, title="[bold] AutoPatch-J 系统驾驶舱 [/]", style="blue")

    def handle_reindex(self) -> None:
        if not self.indexer: return
        self.renderer.print_step("正在重新构建索引...")
        stats = self.indexer.perform_rebuild()
        self.renderer.print_success(f"索引刷新完成 ({stats.get('total', 0)})")

    def handle_apply(self, pending: Any) -> None:
        assert self.patch_engine is not None and self.artifacts is not None
        self.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        if self.patch_engine.perform_apply(pending):
            self.renderer.print_success("物理应用成功！")
            
            from autopatch_j.core.validator_service import SemanticValidator
            from autopatch_j.scanners import get_scanner, DEFAULT_SCANNER_NAME
            scanner = get_scanner(DEFAULT_SCANNER_NAME)
            if scanner:
                validator = SemanticValidator(self.repo_root, scanner)
                success, msg = validator.perform_verification(pending)
                if success: self.renderer.print_success(msg)
                else: self.renderer.print_error(msg)
        else:
            self.renderer.print_error("应用失败。")

    def handle_discard(self) -> None:
        self.renderer.print_info("已丢弃当前补丁草案。")

def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
