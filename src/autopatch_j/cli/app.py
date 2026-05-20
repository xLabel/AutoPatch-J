from __future__ import annotations

import signal
import sys
from pathlib import Path
from typing import Any

from prompt_toolkit import HTML, PromptSession

from autopatch_j.cli.agent_request_runner import AgentRequestRunner
from autopatch_j.cli.agent_stream_presenter import AgentStreamPresenter
from autopatch_j.cli.command_handlers import CommandHandlers
from autopatch_j.cli.command_router import CommandRouter
from autopatch_j.cli.input_controller import CliInputController
from autopatch_j.cli.input_router import UserInputRouter
from autopatch_j.cli.render import DECISION_STYLE, CliRenderer
from autopatch_j.cli.runtime import CliRuntime, build_cli_runtime
from autopatch_j.cli.welcome_presenter import WelcomePresenter
from autopatch_j.cli.workflow_dependencies import WorkflowDependencies
from autopatch_j.config import GlobalConfig, discover_repo_root
from autopatch_j.llm.factory import build_default_llm_client


class AutoPatchCli:
    """
    AutoPatch-J 命令行应用入口。

    职责边界：
    1. 持有主事件循环、欢迎界面、退出信号和 prompt 会话。
    2. 初始化/重置 CliRuntime，并接线 command router 与 input router。
    3. 不承载具体业务规则；命令、输入路由、workflow 和 Agent 展示都由下层组件负责。
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.renderer = CliRenderer()
        self.welcome_presenter = WelcomePresenter(self.renderer)

        self.runtime: CliRuntime | None = None
        self.command_handlers = CommandHandlers(self)
        self.command_router = CommandRouter(self.command_handlers, self.renderer)
        self.input_router: UserInputRouter | None = None
        self.agent_presenter: AgentStreamPresenter | None = None
        self.agent_runner: AgentRequestRunner | None = None
        self.input_controller = CliInputController(
            index_search=lambda query: self.runtime.symbol_indexer.search(query) if self.runtime else [],
            repo_root=self.repo_root,
        )
        self.prompt_session: PromptSession[str] | None = None
        self.is_first_run: bool = False

        if self.repo_root:
            index_db_path = self.repo_root / ".autopatch-j" / "index.db"
            self.is_first_run = not index_db_path.exists()
            self.initialize_runtime(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(self, signum: int, frame: Any) -> None:
        self.request_exit(f"\n[bold {DECISION_STYLE}]收到中断信号，正在退出...[/]")

    def reset_agent_session(self) -> None:
        if self.runtime is not None:
            self.runtime.agent.reset_history()

    def reset_project_state(self) -> None:
        if self.runtime is not None:
            self.runtime.agent.reset_history(clear_memory=True)
            self.runtime.artifact_manager.clear_project_state()
        self.clear_runtime()
        self.is_first_run = True

    def clear_runtime(self) -> None:
        self.runtime = None
        self.input_router = None
        self.agent_presenter = None
        self.agent_runner = None

    def _finalize_cli_exit(self, message: str | None = None) -> None:
        self.reset_agent_session()
        if message:
            self.renderer.print(message)

    def request_exit(self, message: str | None = None) -> None:
        self._finalize_cli_exit(message)
        sys.exit(0)

    def run(self) -> int:
        if not self._ensure_prompt_session():
            return 1
        self.reset_agent_session()

        self.welcome_presenter.render(self.repo_root, self.is_first_run, self.runtime)
        while True:
            try:
                runtime = self.runtime
                workspace = runtime.workspace_manager.load() if runtime else None
                current_item = workspace.current_patch() if workspace else None
                pending_draft = current_item.draft.to_patch_draft() if current_item else None
                current_idx, total_count = workspace.review_progress() if workspace else (0, 0)
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
                        source_hint=pending_draft.source_hint,
                    )
                    prompt_prefix = f"<style fg='{DECISION_STYLE}' font_weight='bold'>PENDING</style> autopatch-j"

                user_input = self.prompt_session.prompt(HTML(f"{prompt_prefix}> ")).strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    self.command_router.handle_command(user_input)
                    continue

                if self.input_router is None:
                    self.renderer.print_error("系统未初始化，请先执行 /init")
                    continue

                if current_item is not None:
                    self.input_router.handle_review_input(user_input, current_item)
                else:
                    self.input_router.handle_chat(user_input)

            except (EOFError, KeyboardInterrupt):
                self._finalize_cli_exit()
                break
            except Exception as exc:
                self._render_runtime_error(exc)

        return 0

    def _render_runtime_error(self, exc: Exception) -> None:
        error_message = str(exc)
        if "401" in error_message or "AuthenticationError" in error_message:
            self.renderer.print_error("LLM 认证失败 (401)，请检查 AUTOPATCH_LLM_API_KEY。")
        elif "403" in error_message or "AccessDenied" in error_message:
            self.renderer.print_error("LLM 模型无访问权限 (403)，请检查模型权限或账户余额。")
        elif "404" in error_message or "NotFoundError" in error_message:
            self.renderer.print_error("LLM 接口未找到 (404)，请检查 AUTOPATCH_LLM_BASE_URL 或 AUTOPATCH_LLM_MODEL。")
        else:
            self.renderer.print_error(f"指令执行异常: {error_message}")

    def _ensure_prompt_session(self) -> bool:
        if self.prompt_session is not None:
            return True
        try:
            self.prompt_session = self.input_controller.create_prompt_session()
            return True
        except Exception as exc:
            self.renderer.print_error(f"CLI 输入环境初始化失败: {exc}")
            return False

    def initialize_runtime(self, repo_root: Path) -> None:
        self.runtime = build_cli_runtime(repo_root, llm_factory=build_default_llm_client)
        self.input_controller.set_repo_root(repo_root)
        self.agent_presenter = AgentStreamPresenter(
            renderer=self.renderer,
            workspace_manager=self.runtime.workspace_manager,
            chat_filter=self.runtime.chat_filter,
            agent=self.runtime.agent,
            describe_current_scope_paths=self.runtime.summary_provider.describe_current_scope_paths,
            build_static_scan_summary=self.runtime.summary_provider.build_static_scan_summary,
            build_local_no_issue_summary=self.runtime.summary_provider.build_local_no_issue_summary,
            debug_mode=lambda: GlobalConfig.debug_mode,
        )
        self.agent_runner = AgentRequestRunner(self.agent_presenter)
        workflow_services = WorkflowDependencies(
            runtime=self.runtime,
            agent_runner=self.agent_runner,
            summary_provider=self.runtime.summary_provider,
            renderer=self.renderer,
            command_router=self.command_router,
            command_handlers=self.command_handlers,
            debug_mode=lambda: GlobalConfig.debug_mode,
        )
        self.input_router = UserInputRouter(workflow_services)


def main() -> int:
    cli = AutoPatchCli(Path.cwd())
    return cli.run()
