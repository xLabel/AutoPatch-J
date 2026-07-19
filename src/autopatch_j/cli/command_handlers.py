from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from rich.table import Table

from autopatch_j.cli.commands import CLI_COMMANDS
from autopatch_j.cli.memory_presenter import MemoryPresenter
from autopatch_j.cli.render import DECISION_STYLE, SYSTEM_STYLE
from autopatch_j.cli.runtime import CliRuntime
from autopatch_j.cli.status_presenter import StatusPresenter
from autopatch_j.config import GlobalConfig
from autopatch_j.core.patching import (
    PatchApplicationResult,
    SearchReplacePatchDraft,
    VerificationOutcome,
)
from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime


class CliHostActions(Protocol):
    """
    命令处理器需要调用的 CLI 主机能力。

    这里保留生命周期动作和当前 runtime 引用，避免命令层依赖完整 app 实现。
    """

    cwd: Path
    repo_root: Path | None
    renderer: Any
    runtime: CliRuntime | None

    def initialize_runtime(self, repo_root: Path) -> None: ...
    def reset_project_state(self) -> None: ...
    def request_exit(self, message: str | None = None) -> None: ...


class CommandHandlers:
    """
    斜杠命令和补丁确认动作的具体处理器。

    它只执行已解析的命令；自然语言路由由 UserInputRouter 负责。
    """

    def __init__(self, host: CliHostActions) -> None:
        self.host = host

    def handle_help(self, args: list[str] | None = None) -> None:
        sys_table = Table(show_header=True, header_style=f"bold {SYSTEM_STYLE}", box=None)
        sys_table.add_column("系统命令", style=SYSTEM_STYLE, width=15)
        sys_table.add_column("功能描述")
        for command in CLI_COMMANDS:
            if command.show_in_help:
                sys_table.add_row(command.name, command.help_description)

        act_table = Table(show_header=True, header_style=f"bold {DECISION_STYLE}", box=None)
        act_table.add_column("交互关键字", style=DECISION_STYLE, width=15)
        act_table.add_column("用法说明")
        act_table.add_row("@符号", "补全文件或目录")
        act_table.add_row("apply", "应用当前补丁预览")
        act_table.add_row("discard", "丢弃当前补丁草案")
        act_table.add_row("abort", "中止审核并丢弃剩余所有补丁")

        self.host.renderer.print_panel("命令帮助", style=SYSTEM_STYLE)
        self.host.renderer.print_table(sys_table)
        self.host.renderer.print_blank()
        self.host.renderer.print_heading("交互说明")
        self.host.renderer.print_table(act_table)

    def handle_reset(self, args: list[str] | None = None) -> None:
        self.host.reset_project_state()
        self.host.renderer.print_success(
            "项目工作台已重置；Memory、Memory 导出和 CLI history 已保留。"
            "如需清空 Memory，请执行 /memory clear --confirm。"
        )

    def handle_scanners(self, args: list[str] | None = None) -> None:
        StatusPresenter(self.host.renderer).render_scanners(self.host.repo_root)

    def handle_quit(self, args: list[str] | None = None) -> None:
        self.host.request_exit()

    def handle_init(self, args: list[str] | None = None) -> None:
        if self.host.repo_root is None:
            self.host.renderer.print_error("未检测到项目根目录，无法初始化。")
            return

        self.host.renderer.print_step("正在初始化 AutoPatch-J 环境...")
        self.host.initialize_runtime(self.host.repo_root)
        runtime = self._require_runtime()
        if runtime is None:
            return
        runtime.workspace_manager.clear()

        status, _ = install_managed_semgrep_runtime()
        self.host.renderer.print_step(f"扫描器运行时自检: {status}")

        stats = runtime.symbol_indexer.rebuild_index()
        self.host.renderer.print_success(f"初始化完成，索引 {stats.get('total', 0)} 项")

        if stats.get("class", 0) == 0 and stats.get("method", 0) == 0:
            self.host.renderer.print_panel(
                "[bold yellow]索引构建完成，但未提取到任何 Java 类或方法！[/]\n"
                "这似乎不是一个标准的 Java 源码项目，AutoPatch-J 的大模型上下文感知能力将严重受限。",
                title="警告",
                style="bold yellow",
            )

    def handle_status(self, args: list[str] | None = None) -> None:
        StatusPresenter(self.host.renderer).render_status(self.host.runtime, self.host.repo_root)

    def handle_reindex(self, args: list[str] | None = None) -> None:
        runtime = self._require_runtime()
        if runtime is None:
            return
        self.host.renderer.print_step("正在重新构建索引...")
        stats = runtime.symbol_indexer.rebuild_index()
        self.host.renderer.print_success(f"索引刷新完成，累计 {stats.get('total', 0)} 项")

    def handle_new(self, args: list[str] | None = None) -> None:
        runtime = self._require_runtime()
        if runtime is None:
            return

        old_thread = runtime.memory_manager.ensure_active_thread()
        self._flush_memory_watermark(runtime, thread_id=old_thread.id)
        had_pending_patch = runtime.workspace_manager.load().has_pending_patch()
        runtime.workspace_manager.clear()
        runtime.agent.reset_history()
        new_thread = runtime.memory_manager.start_new_thread(expected_thread_id=old_thread.id)
        if had_pending_patch:
            self.host.renderer.print_agent_text("已中止待确认补丁并清空 review workspace。")
        self.host.renderer.print_success(f"已创建新的普通对话 thread：{new_thread.id}")

    def handle_memory(self, args: list[str] | None = None) -> None:
        runtime = self._require_runtime()
        if runtime is None:
            return
        command_args = args or []
        if not command_args:
            self._render_memory_usage()
            return

        subcommand = command_args[0].lower()
        rest = command_args[1:]
        presenter = MemoryPresenter(
            self.host.renderer,
            show_raw_errors=GlobalConfig.debug_mode,
        )
        try:
            if subcommand == "status" and not rest:
                presenter.render_status(
                    runtime.memory_manager.status(),
                    runtime.memory_manager.summary_status(),
                )
                return
            if subcommand == "summary" and not rest:
                result = runtime.memory_manager.rebuild_summary()
                status = result.status
                if status.state == "current":
                    self.host.renderer.print_success(
                        "Memory 审阅摘要已更新："
                        f"state=current，active items={status.active_item_count}，"
                        f"path={status.path}。"
                    )
                else:
                    self.host.renderer.print_error(
                        "Memory 审阅摘要更新失败；SQLite Memory 不受影响。"
                        f"状态={status.state}，路径={status.path}。"
                    )
                return
            if subcommand == "list" and not rest:
                presenter.render_list(runtime.memory_manager.list_items())
                return
            if subcommand == "show" and len(rest) == 1:
                presenter.render_detail(runtime.memory_manager.show_item(rest[0]))
                return
            if subcommand == "forget" and len(rest) == 1:
                result = runtime.memory_manager.forget(rest[0])
                if result.forgotten:
                    self.host.renderer.print_success(
                        f"已忘记派生 Memory {result.memory_id}；原始 turn 仍被保留用于审计。"
                    )
                return
            if subcommand == "clear":
                if rest != ["--confirm"]:
                    self.host.renderer.print_error(
                        "清空会删除全部 thread、turn、派生 Memory 和 job；"
                        "确认执行请使用 /memory clear --confirm。"
                    )
                    return
                result = runtime.memory_manager.clear()
                runtime.agent.reset_history()
                self.host.renderer.print_success(
                    "Memory 已清空并创建新 thread "
                    f"{result.active_thread_id}；既有导出与 CLI history 未删除。"
                )
                return
            if subcommand == "export" and not rest:
                result = runtime.memory_manager.export()
                self.host.renderer.print_success(
                    f"RAW Memory 已导出至 {result.path}（未脱敏，不会覆盖既有导出）。"
                )
                return
        except LookupError as exc:
            self.host.renderer.print_error(f"未找到 Memory：{exc}")
            return
        except Exception as exc:
            self.host.renderer.print_error(f"Memory 命令执行失败：{exc}")
            return

        self._render_memory_usage()

    def _flush_memory_once(self, runtime: CliRuntime, reason: str, thread_id: str | None = None) -> None:
        try:
            result = runtime.flush_memory_once(reason=reason, thread_id=thread_id)
        except Exception as exc:
            self.host.renderer.print_error(f"Memory 本次处理失败，任务已保留：{exc}")
            return
        if result.failed or result.pending:
            self.host.renderer.print_error(
                f"Memory 本次处理未完全成功（failed={result.failed}, pending={result.pending}），"
                "剩余任务已保留。"
            )

    def _flush_memory_watermark(
        self,
        runtime: CliRuntime,
        *,
        thread_id: str,
    ) -> None:
        try:
            result = runtime.flush_memory_watermark(
                reason="new",
                thread_id=thread_id,
                wait_seconds=5,
            )
        except Exception as exc:
            self.host.renderer.print_error(
                f"Memory 本次处理失败，任务已保留：{exc}"
            )
            return
        if result.failed or result.pending or result.errors:
            self.host.renderer.print_error(
                "旧 thread Memory 尚未完全物化；任务已保留并在后台继续。"
            )

    def _render_memory_usage(self) -> None:
        self.host.renderer.print_error(
            "用法：/memory status|summary|list|show <id>|forget <id>|clear --confirm|export"
        )

    def handle_apply(self, pending: SearchReplacePatchDraft) -> PatchApplicationResult:
        runtime = self._require_runtime()
        if runtime is None:
            return PatchApplicationResult(
                applied=False,
                error_code="RUNTIME_UNAVAILABLE",
                message="系统未初始化，无法应用补丁。",
            )
        if pending.error_code == "STALE_DRAFT":
            result = PatchApplicationResult(
                applied=False,
                error_code="STALE_DRAFT",
                message=pending.message,
            )
            self.host.renderer.print_error(f"应用失败 [STALE_DRAFT]：{pending.message}")
            return result
        self.host.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        apply_result = runtime.patch_engine.apply_patch(pending)
        if not apply_result.applied:
            error_code = apply_result.error_code or "UNKNOWN"
            self.host.renderer.print_error(f"应用失败 [{error_code}]：{apply_result.message}")
            return apply_result

        self.host.renderer.print_success("补丁已应用")

        if runtime.patch_verifier:
            result = runtime.patch_verifier.verify_finding_resolved(
                pending,
                apply_result,
            )
            if result.outcome is VerificationOutcome.RESOLVED:
                self.host.renderer.print_success(result.message)
            else:
                self.host.renderer.print_error(result.message)
        else:
            self.host.renderer.print_error("无法确认修复结果：未配置补丁验证器。")
        return apply_result

    def handle_discard(self) -> None:
        self.host.renderer.print_agent_text("已丢弃当前草案")

    def _require_runtime(self) -> CliRuntime | None:
        if self.host.runtime is None:
            self.host.renderer.print_error("系统未初始化，请先执行 /init")
            return None
        return self.host.runtime
