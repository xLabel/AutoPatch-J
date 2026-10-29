"""CLI 生命周期、同步输入循环与依赖装配入口。"""

from __future__ import annotations

import asyncio
import pathlib
import signal
import sys
import types

import prompt_toolkit

from autopatch_j import config
from autopatch_j.cli import agent_stream_presenter
from autopatch_j.cli import command_handlers
from autopatch_j.cli import command_router
from autopatch_j.cli import input_controller
from autopatch_j.cli import input_router
from autopatch_j.cli import render
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.cli import welcome_presenter
from autopatch_j.cli import workflow_dependencies
from autopatch_j.llm import diagnostics
from autopatch_j.llm import factory


class AutoPatchCli:
    """AutoPatch-J 命令行应用入口。

    职责边界：
    1. 持有主事件循环、欢迎界面、退出信号和 prompt 会话。
    2. 初始化/重置 CliRuntime，并接线 command router 与 input router。
    3. 不承载具体业务规则；命令、输入路由、workflow 和 Agent 展示都由下层组件负责。
    """

    def __init__(self, cwd: pathlib.Path) -> None:
        """创建当前目录的业务及模型资源并注册 SIGINT，不发送模型请求。"""
        self.cwd = cwd.resolve()
        self.repo_root: pathlib.Path | None = config.discover_repo_root(
            self.cwd
        )
        self.renderer = render.CliRenderer()
        self.welcome_presenter = welcome_presenter.WelcomePresenter(
            self.renderer
        )

        self.runtime: cli_runtime.CliRuntime | None = None
        self.command_handlers = command_handlers.CommandHandlers(self)
        self.command_router = command_router.CommandRouter(
            self.command_handlers, self.renderer
        )
        self.input_router: input_router.UserInputRouter | None = None
        self.agent_presenter: (
            agent_stream_presenter.AgentStreamPresenter | None
        ) = None
        self.agent_runner: (
            agent_stream_presenter.AgentStreamPresenter | None
        ) = None
        self.input_controller = input_controller.CliInputController(
            index_search=lambda query: (
                self.runtime.symbol_indexer.search(query)
                if self.runtime
                else []
            ),
            repo_root=self.repo_root,
        )
        self.prompt_session: prompt_toolkit.PromptSession[str] | None = None
        self.is_first_run: bool = False
        self._exit_finalized = False

        if self.repo_root:
            index_db_path = self.repo_root / ".autopatch-j" / "index.db"
            self.is_first_run = not index_db_path.exists()
            self.initialize_runtime(self.repo_root)

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _handle_interrupt(
        self, signum: int, frame: types.FrameType | None
    ) -> None:
        """有异步操作时请求取消；空闲时用 KeyboardInterrupt 退出。"""
        del signum, frame
        if (
            self.runtime is not None
            and self.runtime.model_runtime.task is not None
        ):
            self.runtime.model_runtime.cancel()
            return
        raise KeyboardInterrupt

    def reset_project_state(self) -> None:
        """关闭当前运行时并清理工作台，保留会话、笔记和导出。"""
        if self.runtime is not None:
            self.runtime.agent.reset_history()
            self.runtime.close()
            self.runtime.artifact_manager.clear_project_state()
        self._detach_runtime()
        self.is_first_run = True

    def clear_runtime(self) -> None:
        """关闭并解除当前项目运行时，保留磁盘数据。"""
        if self.runtime is not None:
            self.runtime.close()
        self._detach_runtime()

    def _detach_runtime(self) -> None:
        self.runtime = None
        self.input_router = None
        self.agent_presenter = None
        self.agent_runner = None

    def _finalize_cli_exit(self, message: str | None = None) -> None:
        """仅执行一次退出收尾，关闭失败报告为次生错误。"""
        if self._exit_finalized:
            return
        self._exit_finalized = True
        runtime = self.runtime
        if runtime is not None:
            try:
                runtime.close()
            # 退出边界保留主失败；收尾异常只显示，不阻止退出。
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.renderer.print_error(f"运行时关闭失败：{exc}")
        if message:
            self.renderer.print_plain(message)

    def request_exit(self, message: str | None = None) -> None:
        """完成资源关闭和退出提示，再终止 CLI。"""
        self._finalize_cli_exit(message)
        sys.exit(0)

    def run(self) -> int:
        """运行交互，用户取消正常退出；任何退出路径均关闭当前运行资源。"""
        try:
            return self._run_interaction()
        except asyncio.CancelledError:
            return 0
        finally:
            self._finalize_cli_exit()

    def _run_interaction(self) -> int:
        """逐条路由输入并展示当前审核项；普通命令错误报告后继续输入。"""
        if not self._ensure_prompt_session():
            self._finalize_cli_exit()
            return 1

        self.welcome_presenter.render(
            self.repo_root, self.is_first_run, self.runtime
        )
        assert self.prompt_session is not None
        while True:
            try:
                runtime = self.runtime
                workspace = (
                    runtime.workspace_manager.load() if runtime else None
                )
                current_item = workspace.current_patch() if workspace else None
                pending_draft = (
                    current_item.draft.to_patch_draft()
                    if current_item
                    else None
                )
                current_idx, total_count = (
                    workspace.review_progress() if workspace else (0, 0)
                )
                prompt_prefix = "autopatch-j"

                if pending_draft:
                    self.renderer.print_diff(
                        pending_draft.diff,
                        title=f" 预览: {pending_draft.file_path} ",
                    )
                    self.renderer.print_action_panel(
                        file_path=pending_draft.file_path,
                        diff=pending_draft.diff,
                        validation=pending_draft.validation.status,
                        rationale=pending_draft.rationale or "无说明",
                        current_idx=current_idx,
                        total_count=total_count,
                        source_hint=pending_draft.source_hint,
                        blocking_error=(
                            pending_draft.message
                            if pending_draft.error_code == "STALE_DRAFT"
                            else None
                        ),
                    )
                    review_state = (
                        "STALE"
                        if pending_draft.error_code == "STALE_DRAFT"
                        else "PENDING"
                    )
                    prompt_prefix = (
                        f"<style fg='{render.DECISION_STYLE}"
                        f"' font_weight='bold'>"
                        f"{review_state}</style> autopatch"
                        f"-j"
                    )

                user_input = self.prompt_session.prompt(
                    prompt_toolkit.HTML(f"{prompt_prefix}> ")
                ).strip()
                if not user_input:
                    continue

                if user_input.startswith("/"):
                    self.command_router.handle_command(user_input)
                    continue

                if self.input_router is None:
                    self.renderer.print_error("系统未初始化，请先执行 /init")
                    continue

                if current_item is not None:
                    self.input_router.handle_review_input(
                        user_input, current_item
                    )
                else:
                    self.input_router.handle_chat(user_input)

            except EOFError, KeyboardInterrupt:
                self._finalize_cli_exit()
                break
            # 终端命令边界报告失败并恢复输入；不把失败转为业务成功。
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._render_runtime_error(exc)

        return 0

    def _render_runtime_error(self, exc: Exception) -> None:
        """按 debug 策略展示有界错误；普通模式不附加供应商 RAW 内容。"""
        raw_error = diagnostics.format_raw_llm_exception(exc)
        if config.GLOBAL_CONFIG.debug_mode:
            self.renderer.print_error(f"指令执行异常: {raw_error}")
            return

        error_message = raw_error
        if "401" in error_message or "AuthenticationError" in error_message:
            self.renderer.print_error(
                "LLM 认证失败 (401)，请检查 AUTOPATCH_LLM_API_KEY。"
            )
        elif "403" in error_message or "AccessDenied" in error_message:
            self.renderer.print_error(
                "LLM 模型无访问权限 (403)，请检查模型权限或账户余额。"
            )
        elif "404" in error_message or "NotFoundError" in error_message:
            self.renderer.print_error(
                "LLM 接口未找到 (404)，请检查 AUTOPATCH_LLM_B"
                "ASE_URL 或 AUTOPATCH_LLM_MODEL。"
            )
        else:
            self.renderer.print_error(
                "指令执行异常；启用 AUTOPATCH_DEBUG=true 查看 RAW 详情。"
            )

    def _ensure_prompt_session(self) -> bool:
        """创建输入会话；环境初始化失败返回 False 供外层退出。"""
        if self.prompt_session is not None:
            return True
        try:
            self.prompt_session = self.input_controller.create_prompt_session()
            return True
        # 交互环境初始化边界：任意后端错误都应报告并退出输入循环。
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.renderer.print_error(f"CLI 输入环境初始化失败: {exc}")
            return False

    def initialize_runtime(self, repo_root: pathlib.Path) -> None:
        """先关闭旧运行时，再为目标项目装配业务服务与路由。"""
        if self.runtime is not None:
            self.runtime.close()
        self.runtime = cli_runtime.build_cli_runtime(
            repo_root, model_factory=factory.build_model_runtime
        )
        self._exit_finalized = False
        self.input_controller.set_repo_root(repo_root)
        self.agent_presenter = agent_stream_presenter.AgentStreamPresenter(
            renderer=self.renderer,
            workspace_manager=self.runtime.workspace_manager,
            chat_filter=self.runtime.chat_filter,
            describe_current_scope_paths=(
                self.runtime.summary_provider.describe_current_scope_paths
            ),
            build_static_scan_summary=(
                self.runtime.summary_provider.build_static_scan_summary
            ),
            build_local_no_issue_summary=(
                self.runtime.summary_provider.build_local_no_issue_summary
            ),
            debug_mode=lambda: config.GLOBAL_CONFIG.debug_mode,
        )
        self.agent_runner = self.agent_presenter
        workflow_services = workflow_dependencies.WorkflowDependencies(
            runtime=self.runtime,
            agent_runner=self.agent_runner,
            summary_provider=self.runtime.summary_provider,
            renderer=self.renderer,
            command_router=self.command_router,
            command_handlers=self.command_handlers,
            debug_mode=lambda: config.GLOBAL_CONFIG.debug_mode,
        )
        self.input_router = input_router.UserInputRouter(workflow_services)


def main() -> int:
    """启动交互式 CLI，并返回进程退出码。"""
    cli = AutoPatchCli(pathlib.Path.cwd())
    return cli.run()
