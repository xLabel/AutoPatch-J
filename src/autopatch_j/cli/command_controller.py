from __future__ import annotations
from pathlib import Path
from typing import Any, Protocol

from rich.table import Table

from autopatch_j.cli.render import DECISION_STYLE, SYSTEM_STYLE
from autopatch_j.config import GlobalConfig
from autopatch_j.core.symbol_indexer import SymbolIndexer
from autopatch_j.core.patch_engine import PatchDraft, PatchEngine
from autopatch_j.core.workspace_manager import WorkspaceManager
from autopatch_j.scanners import ALL_SCANNERS, DEFAULT_SCANNER_NAME, get_scanner
from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime


class CommandControllerContext(Protocol):
    cwd: Path
    repo_root: Path | None
    artifacts: Any
    symbol_indexer: SymbolIndexer | None
    patch_engine: PatchEngine | None
    patch_verifier: Any | None
    workspace_manager: WorkspaceManager | None
    renderer: Any

    def _init_services(self, repo_root: Path) -> None: ...
    def request_exit(self, message: str | None = None) -> None: ...


class CliCommandController:
    """Handle slash commands and patch confirmation commands for the CLI."""

    def __init__(self, context: CommandControllerContext) -> None:
        self.context = context

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
            self.context.request_exit()
        else:
            self.context.renderer.print_error(f"未知命令：{cmd}")

    def handle_help(self) -> None:
        sys_table = Table(show_header=True, header_style=f"bold {SYSTEM_STYLE}", box=None)
        sys_table.add_column("系统命令", style=SYSTEM_STYLE, width=15)
        sys_table.add_column("功能描述")
        sys_table.add_row("/init", "初始化当前目录为 Java 项目并建立索引")
        sys_table.add_row("/status", "查看当前项目状态与索引统计")
        sys_table.add_row("/scanner", "查看扫描器状态")
        sys_table.add_row("/reindex", "重建代码索引")
        sys_table.add_row("/help", "显示命令帮助")
        sys_table.add_row("/quit", "安全退出程序")

        act_table = Table(show_header=True, header_style=f"bold {DECISION_STYLE}", box=None)
        act_table.add_column("交互关键字", style=DECISION_STYLE, width=15)
        act_table.add_column("用法说明")
        act_table.add_row("@符号", "补全文件或目录")
        act_table.add_row("apply", "应用当前补丁预览")
        act_table.add_row("discard", "丢弃当前补丁草案")

        self.context.renderer.print_panel("命令帮助", style=SYSTEM_STYLE)
        self.context.renderer.console.print(sys_table)
        self.context.renderer.print("\n[bold]交互说明[/bold]")
        self.context.renderer.console.print(act_table)

    def handle_scanners(self) -> None:
        table = Table(title="扫描器状态", show_header=True, header_style=f"bold {SYSTEM_STYLE}")
        table.add_column("名称", style=SYSTEM_STYLE, width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("功能简介")

        for scanner in ALL_SCANNERS:
            meta = scanner.get_meta(self.context.repo_root)
            status_text = (
                f"[green]● {meta.status}[/green]"
                if meta.is_implemented
                else f"[dim]● {meta.status}[/dim]"
            )
            table.add_row(meta.name, status_text, meta.version if meta.is_implemented else "-", meta.description)

        self.context.renderer.console.print(table)

    def handle_init(self) -> None:
        self.context.renderer.print_step("正在初始化 AutoPatch-J 环境...")
        self.context.repo_root = self.context.cwd
        self.context._init_services(self.context.repo_root)
        if getattr(self.context, "artifacts", None) is not None:
            self.context.artifacts.clear_pending_patch()

        status, _ = install_managed_semgrep_runtime()
        self.context.renderer.print_step(f"扫描器运行时自检: {status}")

        assert self.context.symbol_indexer is not None
        stats = self.context.symbol_indexer.rebuild_index()
        self.context.renderer.print_success(f"初始化完成，索引 {stats.get('total', 0)} 项")

    def handle_status(self) -> None:
        if not self.context.symbol_indexer or not self.context.workspace_manager:
            self.context.renderer.print_error("系统未初始化，请先执行 /init")
            return

        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", style=SYSTEM_STYLE, width=15)
        table.add_column("Value")

        table.add_row("[bold]项目根目录[/]", str(self.context.repo_root))
        table.add_row("[bold]LLM 模型[/]", f"{GlobalConfig.llm_model} ([dim]{GlobalConfig.llm_base_url}[/])")

        scanner = get_scanner(DEFAULT_SCANNER_NAME)
        scanner_meta = scanner.get_meta(self.context.repo_root) if scanner else None
        scanner_status = (
            f"[green]就绪 ({scanner_meta.version})[/]"
            if scanner_meta and scanner_meta.is_implemented and "就绪" in scanner_meta.status
            else "[red]未就绪[/]"
        )
        table.add_row("[bold]静态扫描器[/]", scanner_status)

        pending = self.context.workspace_manager.get_current_patch()
        buffer_status = (
            f"[bold yellow]存在待确认补丁 ({pending.file_path})[/]"
            if pending
            else "[dim]空闲[/]"
        )
        table.add_row("[bold]补丁缓冲区[/]", buffer_status)

        stats = self.context.symbol_indexer.get_stats()
        stats_str = (
            f"文件:{stats.get('file', 0)} | 类:{stats.get('class', 0)} | "
            f"方法:{stats.get('method', 0)} (总计:{stats.get('total', 0)})"
        )
        table.add_row("[bold]符号索引[/]", stats_str)
        symbol_status = self.context.symbol_indexer.fetch_symbol_extract_status()
        symbol_mode = str(symbol_status.get("mode", "full"))
        if symbol_mode == "degraded":
            status_text = "[yellow]已降级[/]"
            last_error = str(symbol_status.get("last_error") or "")
            if last_error:
                status_text += f" [dim]({last_error})[/]"
        else:
            status_text = "[green]正常[/]"
        table.add_row("[bold]符号提取[/]", status_text)

        self.context.renderer.print_panel(table, title="[bold] 项目状态 [/]", style=SYSTEM_STYLE)

    def handle_reindex(self) -> None:
        if not self.context.symbol_indexer:
            return
        self.context.renderer.print_step("正在重新构建索引...")
        stats = self.context.symbol_indexer.rebuild_index()
        self.context.renderer.print_success(f"索引刷新完成，累计 {stats.get('total', 0)} 项")

    def handle_apply(self, pending: PatchDraft) -> None:
        assert self.context.patch_engine is not None
        self.context.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        if not self.context.patch_engine.apply_patch(pending):
            self.context.renderer.print_error("应用失败。")
            return

        self.context.renderer.print_success("补丁已应用")
        
        if self.context.patch_verifier:
            result = self.context.patch_verifier.verify_finding_resolved(pending)
            if result.is_resolved:
                self.context.renderer.print_success(result.message)
            else:
                self.context.renderer.print_error(result.message)

    def handle_discard(self) -> None:
        self.context.renderer.print_info("已丢弃当前草案")
