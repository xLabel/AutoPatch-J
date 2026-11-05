"""解析后的命令动作及人工决定的终端反馈。"""

from __future__ import annotations

import pathlib
import sqlite3
from typing import Protocol

from pydantic_ai_harness import memory as sdk_memory
from rich import table

from autopatch_j.cli import commands
from autopatch_j.cli import render
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.cli import status_presenter
from autopatch_j.core.repair import models
from autopatch_j.scanners.semgrep import runtime as semgrep_runtime


class CliHostActions(Protocol):
    """命令处理器需要调用的 CLI 主机能力。

    这里保留生命周期动作和当前 runtime 引用，避免命令层依赖完整 app 实现。
    """

    cwd: pathlib.Path
    repo_root: pathlib.Path | None
    renderer: render.CliRenderer
    runtime: cli_runtime.CliRuntime | None

    def initialize_runtime(self, repo_root: pathlib.Path) -> None:
        """为目标项目装配运行时并交还旧资源。"""
        ...

    def reset_project_state(self, expected: models.RepairState) -> None:
        """清理工作台并关闭运行时，保留独立用户数据。"""
        ...

    def request_exit(self, message: str | None = None) -> None:
        """完成资源关闭后退出 CLI。"""
        ...


class CommandHandlers:
    """斜杠命令和补丁确认动作的具体处理器。

    它只执行已解析的命令；自然语言路由由 UserInputRouter 负责。
    """

    def __init__(self, host: CliHostActions) -> None:
        self.host = host
        self.displayed_state: models.RepairState | None = None

    def handle_help(self, args: list[str] | None = None) -> None:
        """展示已注册系统命令与人工审核操作。"""
        del args
        sys_table = table.Table(
            show_header=True,
            header_style=f"bold {render.SYSTEM_STYLE}",
            box=None,
        )
        sys_table.add_column("系统命令", style=render.SYSTEM_STYLE, width=15)
        sys_table.add_column("功能描述")
        for command in commands.CLI_COMMANDS:
            if command.show_in_help:
                sys_table.add_row(command.name, command.help_description)

        act_table = table.Table(
            show_header=True,
            header_style=f"bold {render.DECISION_STYLE}",
            box=None,
        )
        act_table.add_column(
            "交互关键字", style=render.DECISION_STYLE, width=15
        )
        act_table.add_column("用法说明")
        act_table.add_row("@符号", "补全文件或目录")
        act_table.add_row("apply", "应用当前补丁预览")
        act_table.add_row("discard", "丢弃当前补丁草案")
        act_table.add_row("abort", "结束当前任务，不回滚已经写入的源码")
        act_table.add_row("allow", "批准已展示的确切写文件范围")

        self.host.renderer.print_panel("命令帮助", style=render.SYSTEM_STYLE)
        self.host.renderer.print_table(sys_table)
        self.host.renderer.print_blank()
        self.host.renderer.print_heading("交互说明")
        self.host.renderer.print_table(act_table)

    def handle_reset(self, args: list[str] | None = None) -> None:
        """重置工作台并展示保留数据的边界。"""
        del args
        runtime = self._require_runtime()
        if runtime is None:
            return
        self.host.reset_project_state(
            self.displayed_state or runtime.repair.store.load()
        )
        self.host.renderer.print_success(
            "项目工作台已重置；项目会话、项目笔记、导出和终端历史已保留。"
            "如需清空 Memory，请执行 /memory clear --"
            "confirm。"
        )

    def handle_scanners(self, args: list[str] | None = None) -> None:
        """展示实际扫描器实现及可用性，不触发安装。"""
        del args
        status_presenter.StatusPresenter(self.host.renderer).render_scanners(
            self.host.repo_root
        )

    def handle_quit(self, args: list[str] | None = None) -> None:
        """完成退出收尾，再结束 CLI 进程。"""
        del args
        self.host.request_exit()

    def handle_init(self, args: list[str] | None = None) -> None:
        """准备受管扫描器并重建索引；保留当前任务。"""
        del args
        if self.host.repo_root is None:
            self.host.renderer.print_error("未检测到项目根目录，无法初始化。")
            return

        self.host.renderer.print_step("正在初始化 AutoPatch-J 环境...")
        self.host.initialize_runtime(self.host.repo_root)
        runtime = self._require_runtime()
        if runtime is None:
            return

        status, _ = semgrep_runtime.install_managed_semgrep_runtime()
        self.host.renderer.print_step(f"扫描器运行时自检: {status}")

        stats = runtime.symbol_indexer.rebuild_index()
        self.host.renderer.print_success(
            f"初始化完成，索引 {stats.get("total", 0)} 项"
        )

        if stats.get("class", 0) == 0 and stats.get("method", 0) == 0:
            self.host.renderer.print_panel(
                "[bold yellow]索引构建完成，但未提取到任何 Java"
                " 类或方法！[/]\n"
                "这似乎不是一个标准的 Java 源码项目，AutoPatch-J"
                " 的大模型上下文感知能力将严重受限。",
                title="警告",
                style="bold yellow",
            )

    def handle_status(self, args: list[str] | None = None) -> None:
        """展示当前配置和工作台诊断，不发送模型请求。"""
        del args
        status_presenter.StatusPresenter(self.host.renderer).render_status(
            self.host.runtime, self.host.repo_root
        )

    def handle_reindex(self, args: list[str] | None = None) -> None:
        """重建当前项目符号索引，保留会话和审核工作台。"""
        del args
        runtime = self._require_runtime()
        if runtime is None:
            return
        self.host.renderer.print_step("正在重新构建索引...")
        stats = runtime.symbol_indexer.rebuild_index()
        self.host.renderer.print_success(
            f"索引刷新完成，累计 {stats.get("total", 0)} 项"
        )

    def handle_new(self, args: list[str] | None = None) -> None:
        """归档项目会话、清理待审草案和临时要求，保留长期笔记。"""
        del args
        runtime = self._require_runtime()
        if runtime is None:
            return

        runtime.repair.end(self.displayed_state or runtime.repair.store.load())
        conversation_id = runtime.model_runtime.run(runtime.conversations.new())
        self.host.renderer.print_success(
            f"已开启新项目会话：{conversation_id}；项目笔记已保留。"
        )

    def handle_memory(self, args: list[str] | None = None) -> None:
        """执行显式笔记管理操作；冲突或存储异常准确显示为未完成。"""
        runtime = self._require_runtime()
        if runtime is None:
            return
        command, *rest = args or [""]
        run = runtime.model_runtime.run
        notebook = runtime.memory
        renderer = self.host.renderer
        try:
            if command in {"status", "list"} and not rest:
                paths = run(notebook.paths())
                renderer.print_agent_text(
                    "项目笔记：" + ("、".join(paths) if paths else "空")
                )
            elif command == "show" and len(rest) <= 1:
                item = run(notebook.show(rest[0] if rest else "MEMORY.md"))
                renderer.print_agent_text(
                    item.content if item else "未找到该笔记。"
                )
            elif command == "forget" and len(rest) == 1:
                run(notebook.forget(rest[0]))
                renderer.print_success(
                    "笔记已删除；项目会话中的历史内容仍保留。"
                )
            elif command == "clear" and rest == ["--confirm"]:
                run(notebook.clear())
                renderer.print_success(
                    "项目笔记已清空；项目会话、导出和终端历史仍保留。"
                )
            elif command == "export" and not rest:
                destination = run(notebook.export())
                renderer.print_success(f"项目笔记已导出：{destination}")
            else:
                renderer.print_error(
                    "用法：/memory status|list|show [path]|"
                    "forget <path>|clear --confirm|expor"
                    "t"
                )
        except (
            sqlite3.DatabaseError,
            OSError,
            LookupError,
            ValueError,
            sdk_memory.MemoryConflictError,
        ) as exc:
            renderer.print_error(f"Memory 操作未完成：{exc}")

    def _require_runtime(self) -> cli_runtime.CliRuntime | None:
        if self.host.runtime is None:
            self.host.renderer.print_error("系统未初始化，请先执行 /init")
            return None
        return self.host.runtime
