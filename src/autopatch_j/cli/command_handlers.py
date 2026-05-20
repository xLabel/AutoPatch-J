from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from rich.table import Table

from autopatch_j.cli.render import DECISION_STYLE, SYSTEM_STYLE
from autopatch_j.cli.runtime import CliRuntime
from autopatch_j.cli.status_presenter import StatusPresenter
from autopatch_j.core.patching import SearchReplacePatchDraft
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

    def handle_help(self) -> None:
        sys_table = Table(show_header=True, header_style=f"bold {SYSTEM_STYLE}", box=None)
        sys_table.add_column("系统命令", style=SYSTEM_STYLE, width=15)
        sys_table.add_column("功能描述")
        sys_table.add_row("/init", "初始化当前目录为 Java 项目并建立索引")
        sys_table.add_row("/status", "查看当前项目状态与索引统计")
        sys_table.add_row("/scanner", "查看扫描器状态")
        sys_table.add_row("/reindex", "重建代码索引")
        sys_table.add_row("/reset", "重置工作台状态与对话历史")
        sys_table.add_row("/help", "显示命令帮助")
        sys_table.add_row("/quit", "安全退出程序")

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

    def handle_reset(self) -> None:
        self.host.reset_project_state()
        self.host.renderer.print_success("项目状态已重置，请执行 /init 重新初始化。")

    def handle_scanners(self) -> None:
        StatusPresenter(self.host.renderer).render_scanners(self.host.repo_root)

    def handle_init(self) -> None:
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

    def handle_status(self) -> None:
        runtime = self._require_runtime()
        if runtime is None:
            return

        StatusPresenter(self.host.renderer).render_project_status(runtime, self.host.repo_root)

    def handle_reindex(self) -> None:
        runtime = self._require_runtime()
        if runtime is None:
            return
        self.host.renderer.print_step("正在重新构建索引...")
        stats = runtime.symbol_indexer.rebuild_index()
        self.host.renderer.print_success(f"索引刷新完成，累计 {stats.get('total', 0)} 项")

    def handle_apply(self, pending: SearchReplacePatchDraft) -> None:
        runtime = self._require_runtime()
        if runtime is None:
            return
        self.host.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        if not runtime.patch_engine.apply_patch(pending):
            self.host.renderer.print_error("应用失败。")
            return

        self.host.renderer.print_success("补丁已应用")

        if runtime.patch_verifier:
            result = runtime.patch_verifier.verify_finding_resolved(pending)
            if result.is_resolved:
                self.host.renderer.print_success(result.message)
            else:
                self.host.renderer.print_error(result.message)

    def handle_discard(self) -> None:
        self.host.renderer.print_agent_text("已丢弃当前草案")

    def _require_runtime(self) -> CliRuntime | None:
        if self.host.runtime is None:
            self.host.renderer.print_error("系统未初始化，请先执行 /init")
            return None
        return self.host.runtime
