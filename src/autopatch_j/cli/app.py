"""CLI 生命周期、同步输入循环与依赖装配入口。"""

from __future__ import annotations

import asyncio
import errno
import pathlib
import signal
import socket
import sqlite3
import ssl
import sys
import types

import filelock
import openai
import prompt_toolkit
from prompt_toolkit import buffer as prompt_buffer
from prompt_toolkit.application import current as prompt_current
from prompt_toolkit.application import run_in_terminal
from pydantic_ai import exceptions as sdk_errors

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.application import runtime as project_runtime
from autopatch_j.cli import command_handlers
from autopatch_j.cli import command_router
from autopatch_j.cli import input_controller
from autopatch_j.cli import input_router
from autopatch_j.cli import render
from autopatch_j.cli import welcome_presenter
from autopatch_j.core import errors
from autopatch_j.core.project import symbol_index
from autopatch_j.core.project import symbols
from autopatch_j.core.repair import models as repair_models
from autopatch_j.core.repair import status as repair_status
from autopatch_j.llm import diagnostics
from autopatch_j.llm import factory
from autopatch_j.llm import policy
from autopatch_j.scanners import catalog


class AutoPatchCli:
    """AutoPatch-J 命令行应用入口。

    职责边界：
    1. 持有主事件循环、欢迎界面、退出信号和 prompt 会话。
    2. 接线应用运行时、命令及输入路由，确保退出收尾。
    3. 业务规则及跨服务维护由应用与领域组件负责。
    """

    def __init__(self, cwd: pathlib.Path, settings: config.AppConfig) -> None:
        """创建当前目录的业务及模型资源并注册 SIGINT，不发送模型请求。"""
        startup_error: Exception | None = None
        try:
            self.cwd = cwd.resolve()
            self.repo_root: pathlib.Path | None = config.discover_repo_root(
                self.cwd
            )
        except OSError as exc:
            self.cwd = cwd
            self.repo_root = None
            startup_error = exc
        self.settings = settings
        self.scanners = catalog.ScannerCatalog.default(
            version=settings.semgrep_version, timeout=settings.scanner_timeout
        )
        self.renderer = render.CliRenderer()
        self.welcome_presenter = welcome_presenter.WelcomePresenter(
            self.renderer, debug_mode=settings.debug_mode
        )

        self.runtime: project_runtime.ProjectRuntime | None = None
        self.command_handlers = command_handlers.CommandHandlers(self)
        self.command_router = command_router.CommandRouter(
            self.command_handlers, self.renderer
        )
        self.input_router: input_router.UserInputRouter | None = None
        self.input_controller = input_controller.CliInputController(
            index_search=self._complete_paths,
            repo_root=self.repo_root,
            on_error=self._report_input_error,
        )
        self.prompt_session: prompt_toolkit.PromptSession[str] | None = None
        self.is_first_run: bool = False
        self._exit_finalized = False
        self._exit_code = 0
        self._active_input_application: object | None = None
        self.runtime_unavailable_reason = error_messages.RUNTIME_CLOSED
        self._startup_error = startup_error
        if startup_error is not None:
            self.runtime_unavailable_reason = error_messages.startup_failed(
                self._reason(startup_error) or "无法访问项目目录"
            )

        if self.repo_root:
            index_db_path = self.repo_root / ".autopatch-j" / "index.db"
            try:
                self.is_first_run = not index_db_path.exists()
                self.initialize_runtime(self.repo_root)
            # 启动项目服务失败不影响已经建立的终端壳。
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._startup_error = exc

        signal.signal(signal.SIGINT, self._handle_interrupt)

    def _complete_paths(self, query: str) -> list[symbols.SymbolIndexEntry]:
        """补全可缺省；不适用的索引在欢迎页与状态页明确报告。"""
        if self.runtime is None:
            return []
        return self.runtime.symbol_index.search(
            query, kinds=["file", "dir"]
        ).entries

    async def _report_input_error(self, operation: str, exc: Exception) -> None:
        """在原生输入应用仍活动时安全打印，保留 buffer 及游标。"""
        # 后台终端输出隔离边界，任意输出故障都须通知同步退出边界。
        # pylint: disable=broad-exception-caught
        try:
            await run_in_terminal(
                lambda: self._render_runtime_error(exc, operation)
            )
        # 此处隔离后台输出失败并通知同步终端退出边界。
        # pylint: disable=broad-exception-caught
        except Exception as output_error:
            # 基本终端输出也失败时才结束当前输入应用，交同步退出边界收尾。
            prompt_current.get_app().exit(exception=output_error)

    def _prepare_prompt(
        self, unused_buffer: prompt_buffer.Buffer | None = None
    ) -> None:
        """接管原生输入应用的未处理后台错误，避免默认堆栈和回车弹屏。"""
        application = prompt_current.get_app_or_none()
        if (
            application is None
            or not application.is_running
            or application is self._active_input_application
        ):
            return
        self._active_input_application = application
        loop = asyncio.get_running_loop()
        reported = False

        def report(
            unused_loop: asyncio.AbstractEventLoop, context: dict[str, object]
        ) -> None:
            nonlocal reported
            error = context.get("exception")
            if isinstance(error, BaseException) and not isinstance(
                error, Exception
            ):
                if not application.is_done:
                    application.exit(exception=error)
                return
            if reported:
                return
            reported = True
            if not isinstance(error, Exception):
                error = RuntimeError(error_messages.INTERNAL_FAILURE)
            if application.is_done:
                self._render_runtime_error(error, "输入辅助功能")
            else:
                application.create_background_task(
                    self._report_input_error("输入辅助功能", error)
                )

        loop.set_exception_handler(report)

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

    def reset_project_state(self, expected: repair_models.RepairState) -> None:
        """关闭当前运行时并清理工作台，保留会话、笔记和导出。"""
        if self.runtime is not None:
            try:
                self.runtime.reset(expected)
            finally:
                if self.runtime.model_runtime.closed:
                    self._detach_runtime()
        self._detach_runtime()
        self.is_first_run = True

    def clear_runtime(self) -> None:
        """关闭并解除当前项目运行时，保留磁盘数据。"""
        try:
            if self.runtime is not None:
                self.runtime.close()
        finally:
            self._detach_runtime()

    def _detach_runtime(self) -> None:
        self.runtime = None
        self.input_router = None
        self.command_handlers.displayed_state = None
        self.runtime_unavailable_reason = error_messages.RUNTIME_CLOSED

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
                self._exit_code = 1
                self._print_error(
                    error_messages.close_failed(
                        self._reason(exc) or "程序内部错误", exiting=True
                    )
                )
                for note in getattr(exc, "__notes__", ()):
                    self._print_error(note, plain=True)
        if message:
            self.renderer.print_plain(message)

    def request_exit(self, message: str | None = None) -> None:
        """完成资源关闭和退出提示，再终止 CLI。"""
        self._finalize_cli_exit(message)
        sys.exit(self._exit_code)

    def run(self) -> int:
        """运行交互，用户取消正常退出；任何退出路径均关闭当前运行资源。"""
        try:
            code = self._run_interaction()
        except asyncio.CancelledError, EOFError, KeyboardInterrupt:
            code = 0
        # 业务和辅助错误已在各操作边界隔离；此处仅处理终端自身无法维持。
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._print_error(
                error_messages.input_unavailable(
                    self._reason(exc) or "程序内部错误"
                )
            )
            code = 1
        finally:
            self._finalize_cli_exit()
        return max(code, self._exit_code)

    def _run_interaction(self) -> int:
        """逐条路由输入并展示当前审核项；普通命令错误报告后继续输入。"""
        if not self._ensure_prompt_session():
            self._finalize_cli_exit()
            return 1

        if self._startup_error is not None:
            self._render_runtime_error(self._startup_error, "项目服务启动")
            self._startup_error = None
        try:
            self.welcome_presenter.render(
                self.repo_root, self.is_first_run, self.runtime
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._render_runtime_error(exc, "界面内容显示")
        self._refresh_display()
        assert self.prompt_session is not None
        while True:
            displayed = self.command_handlers.displayed_state
            prompt_prefix = "autopatch-j"
            if displayed is not None:
                prompt_prefix = f"{displayed.status} autopatch-j"
            # 读取输入的故障与业务操作分开，终端真正不可用时交外层退出。
            self._active_input_application = None
            try:
                user_input = self.prompt_session.prompt(
                    prompt_toolkit.HTML(f"{prompt_prefix}> "),
                    pre_run=self._prepare_prompt,
                ).strip()
            except EOFError, KeyboardInterrupt:
                break
            for (
                history_operation,
                history_error,
            ) in self.input_controller.take_history_errors():
                self._render_runtime_error(history_error, history_operation)
            if not user_input:
                continue
            command = user_input.split(maxsplit=1)[0].lower()
            operation = "Agent" if not user_input.startswith("/") else command
            if command == "/memory":
                arguments = user_input.split()
                operation = {
                    "status": "笔记列表读取",
                    "list": "笔记列表读取",
                    "show": "笔记读取",
                    "forget": "笔记删除",
                    "clear": "笔记清空",
                    "export": "笔记导出",
                }.get(arguments[1] if len(arguments) > 1 else "", "笔记操作")
            try:
                if command in {
                    "apply",
                    "allow",
                    "discard",
                    "abort",
                    "/new",
                    "/reset",
                }:
                    if displayed is None:
                        if self._refresh_display():
                            self._print_error(error_messages.REVIEW_REQUIRED)
                        elif self.runtime is None:
                            self._print_error(self.runtime_unavailable_reason)
                        continue
                if user_input.startswith("/"):
                    self.command_router.handle_command(user_input)
                    if command in {"/init", "/status", "/new", "/reset"}:
                        self._refresh_display()
                    continue

                if self.input_router is None:
                    self._print_error(self.runtime_unavailable_reason)
                    continue
                if displayed is None:
                    if not self._refresh_display():
                        continue
                    displayed = self.command_handlers.displayed_state
                assert displayed is not None
                operation = {
                    "apply": "补丁应用",
                    "allow": "写入范围批准",
                    "discard": "提案丢弃",
                    "abort": "任务结束",
                }.get(command, "Agent")
                self.input_router.handle_input(user_input, displayed)
                self._refresh_display()
            except repair_models.RepairError as exc:
                self._render_runtime_error(exc, operation)
                self._refresh_display()
            # 当前操作隔离边界；故障不能留下可供再次审核的旧画面。
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.command_handlers.displayed_state = None
                self._render_runtime_error(exc, operation)
            finally:
                if (
                    self.runtime is not None
                    and self.runtime.model_runtime.closed
                ):
                    self._detach_runtime()

        return 0

    def _refresh_display(self) -> bool:
        """完整读取和展示成功后才发布人工操作的快照。"""
        self.command_handlers.displayed_state = None
        if self.runtime is None:
            return False
        operation = "任务状态读取"
        try:
            displayed = self.runtime.repair.store.load()
            operation = "提案预览"
            self._render_repair(displayed)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._render_runtime_error(exc, operation)
            return False
        self.command_handlers.displayed_state = displayed
        return True

    def _print_error(self, message: str, *, plain: bool = False) -> None:
        """富文本展示不可用时使用基本错误输出；基本输出失败继续传播。"""
        try:
            if plain:
                self.renderer.print_plain(message)
            else:
                self.renderer.print_error(message)
        except Exception:  # pylint: disable=broad-exception-caught
            print(message, file=sys.stderr)

    @staticmethod
    def _reason(exc: Exception) -> str | None:
        """只识别异常类型及公开状态字段，不猜测异常正文中的数字。"""
        if isinstance(exc, sqlite3.Error):
            code = getattr(exc, "sqlite_errorcode", 0) & 255
            if code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                return error_messages.DATABASE_BUSY
            if code in {sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB}:
                return error_messages.DATABASE_INVALID
            return error_messages.DATABASE_FAILED
        if isinstance(exc, PermissionError):
            return (
                error_messages.permission_denied(str(exc.filename))
                if exc.filename
                else error_messages.PERMISSION_DENIED
            )
        if isinstance(exc, FileNotFoundError):
            return (
                error_messages.missing_file(str(exc.filename))
                if exc.filename
                else error_messages.FILE_MISSING
            )
        if isinstance(exc, ssl.SSLCertVerificationError):
            return error_messages.CERTIFICATE_FAILED
        if isinstance(exc, socket.gaierror):
            return error_messages.DNS_FAILED
        if isinstance(exc, OSError):
            if exc.errno == errno.ENOSPC:
                return error_messages.NO_SPACE
            if exc.errno == errno.EROFS:
                return error_messages.READ_ONLY
            return error_messages.SYSTEM_OPERATION_FAILED
        return None

    def _render_runtime_error(
        self, exc: Exception, operation: str = "操作"
    ) -> None:
        """按 debug 策略展示有界错误；普通模式不附加供应商 RAW 内容。"""
        for note in getattr(exc, "__notes__", ()):
            self._print_error(note, plain=True)
        reason = self._reason(exc)
        if self.runtime is None and operation in {"/init", "项目服务启动"}:
            message = self.runtime_unavailable_reason
        elif isinstance(
            exc, (repair_models.RepairError, errors.OperationError)
        ):
            message = str(exc)
        elif isinstance(exc, symbol_index.IndexNotReadyError):
            message = str(exc)
        elif isinstance(exc, filelock.Timeout):
            message = (
                error_messages.SCANNER_LOCK_TIMEOUT
                if operation == "/init"
                else error_messages.LOCK_TIMEOUT
            )
        elif isinstance(exc, UnicodeError):
            message = error_messages.failed(operation, "文本无法无损解码")
        elif isinstance(exc, (openai.APITimeoutError, TimeoutError)):
            message = (
                error_messages.stopped(operation, error_messages.MODEL_TIMEOUT)
                if operation == "Agent"
                else error_messages.failed(
                    operation, error_messages.OPERATION_TIMEOUT
                )
            )
        elif isinstance(exc, openai.APIConnectionError):
            message = error_messages.stopped(
                operation, error_messages.MODEL_CONNECTION_FAILED
            )
        elif isinstance(
            exc, (openai.APIStatusError, sdk_errors.ModelHTTPError)
        ):
            status = exc.status_code
            reason = {
                401: error_messages.MODEL_AUTH_FAILED,
                403: error_messages.MODEL_ACCESS_DENIED,
                404: error_messages.MODEL_NOT_FOUND,
                429: error_messages.MODEL_RATE_LIMITED,
            }.get(status, error_messages.model_status(status))
            message = error_messages.stopped(operation, reason)
        elif isinstance(exc, policy.ModelFailure):
            message = error_messages.stopped(
                operation,
                (
                    error_messages.MODEL_UNFINISHED
                    if exc.finish_reason != "length"
                    else error_messages.MODEL_TRUNCATED
                ),
            )
        elif isinstance(exc, openai.APIResponseValidationError):
            message = error_messages.stopped(
                operation, error_messages.MODEL_INVALID_RESPONSE
            )
        elif isinstance(exc, sdk_errors.UnexpectedModelBehavior):
            message = error_messages.stopped(
                operation, error_messages.MODEL_UNFINISHED
            )
        elif reason is not None:
            message = (
                error_messages.stopped(operation, reason)
                if operation == "Agent"
                else error_messages.failed(operation, reason)
            )
        else:
            message = error_messages.internal_error(operation)
        if operation == "路径补全":
            message = error_messages.completion_failed(
                reason or str(exc)
                if isinstance(exc, errors.OperationError)
                else reason or "索引无法读取"
            )
        self._print_error(message)
        if self.settings.debug_mode:
            self._print_error(
                diagnostics.format_raw_llm_exception(exc), plain=True
            )

    def _ensure_prompt_session(self) -> bool:
        """创建输入会话；环境初始化失败返回 False 供外层退出。"""
        if self.prompt_session is not None:
            if isinstance(
                self.prompt_session.default_buffer, prompt_buffer.Buffer
            ):
                self.prompt_session.default_buffer.on_text_changed += (
                    self._prepare_prompt
                )
            return True
        try:
            self.prompt_session = self.input_controller.create_prompt_session()
            self.prompt_session.default_buffer.on_text_changed += (
                self._prepare_prompt
            )
            return True
        # 交互环境初始化边界：任意后端错误都应报告并退出输入循环。
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._print_error(
                error_messages.input_unavailable(
                    self._reason(exc) or "程序内部错误"
                )
            )
            return False

    def initialize_runtime(self, repo_root: pathlib.Path) -> None:
        """先关闭旧运行时，再为目标项目装配业务服务与路由。"""
        if self.runtime is not None:
            try:
                self.runtime.close()
            finally:
                self._detach_runtime()
        try:
            self.runtime = project_runtime.build_project_runtime(
                repo_root,
                self.settings,
                model_factory=lambda: factory.build_model_runtime(
                    self.settings
                ),
                scanners=self.scanners,
            )
        except Exception as exc:
            reason = self.settings.llm_unavailable_reason()
            self.runtime_unavailable_reason = (
                error_messages.unavailable("Agent", reason)
                if reason
                else error_messages.startup_failed(
                    str(exc)
                    if isinstance(exc, errors.OperationError)
                    else self._reason(exc) or "程序内部错误"
                )
            )
            raise
        self.runtime_unavailable_reason = ""
        self._exit_finalized = False
        self.input_controller.set_repo_root(repo_root)
        self.input_controller.resume_path_completion()
        self.input_router = input_router.UserInputRouter(
            self.runtime, self.renderer
        )

    def _render_repair(self, state: repair_models.RepairState) -> None:
        """完整展示当前提案与范围申请，中断时展示实际 bytes 核查，不自动恢复。"""
        if state.goal:
            self.renderer.print_plain(f"修复目标：{state.goal}")
        if state.last_apply_error:
            self.renderer.print_error(state.last_apply_error)
        if state.constraints:
            self.renderer.print_plain(
                "当前有效要求：" + "；".join(state.constraints)
            )
        if state.requested_paths:
            self.renderer.print_plain(
                "待批准写范围：" + "、".join(state.requested_paths)
            )
            self.renderer.print_plain(
                "输入 allow 批准上述文件；读取或解释不会扩大写权限。"
            )
        if state.proposal is not None and state.status == "pending":
            self.renderer.print_plain(f"待审修复：{state.proposal.rationale}")
            for item in state.proposal.files:
                self.renderer.print_diff(item.diff, title=f"预览: {item.path}")
                self.renderer.print_plain(
                    f"{item.syntax.status}: {item.syntax.message}"
                )
            self.renderer.print_plain(
                "apply 应用完整提案；discard/abort 结束任务；自然语言可继续解释或修订。"
            )
        if state.status in {"applying", "interrupted"}:
            self.renderer.print_error(
                "任务写入未完成，已停止自动执行。请核查以下事实后再 abort/new；不会自动回滚或重放。"
            )
            if self.runtime is not None:
                for item in self.runtime.repair.inspect(state):
                    self.renderer.print_plain(
                        f"{item.path}: {item.status} — {item.message}"
                    )
        if state.status == "applied":
            self.renderer.print_plain(
                "上一项完整提案已应用；以下为当时保存的验证结果。"
            )
            if not state.checks:
                self.renderer.print_plain(
                    "验证结果尚未保存，不能据此判断问题已解决。"
                )
            for check in state.checks:
                self.renderer.print_plain(
                    f"{check.kind}: {check.status} — {check.message}"
                )
            if self.runtime is not None:
                projected = repair_status.describe(
                    self.runtime.repair.root, state, state
                )
                if any(
                    item.status != "current"
                    for item in projected.source_evidence
                ):
                    self.renderer.print_error(projected.verification_notice)
        if state.assessment is not None:
            assessment = state.assessment
            self.renderer.print_plain(
                f"问题判断：{assessment.outcome} — {assessment.reason}"
            )


def main() -> int:
    """启动交互式 CLI，并返回进程退出码。"""
    cli = AutoPatchCli(pathlib.Path("."), config.AppConfig.from_env())
    return cli.run()
