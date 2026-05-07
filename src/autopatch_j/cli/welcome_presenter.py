from __future__ import annotations

from pathlib import Path

from autopatch_j.cli.render import SYSTEM_STYLE, CliRenderer
from autopatch_j.cli.runtime import CliRuntime
from autopatch_j.config import GlobalConfig


class WelcomePresenter:
    """Renders the startup panel so AutoPatchCli can focus on process lifecycle."""

    def __init__(self, renderer: CliRenderer) -> None:
        self.renderer = renderer

    def render(self, repo_root: Path | None, is_first_run: bool, runtime: CliRuntime | None) -> None:
        if repo_root is None:
            self.renderer.print_panel(
                "AutoPatch-J: Java 安全与正确性修复智能体\n"
                f"{self._debug_mode_hint()}"
                "输入 /help 查看命令，使用 @ 符号绑定上下文。",
                title="AutoPatch-J",
                style=SYSTEM_STYLE,
            )
            self.renderer.print_agent_text("未检测到有效目录，请进入项目目录后执行 /init")
            return

        if is_first_run:
            self.renderer.print_panel(
                f"当前项目: {repo_root}\n"
                f"{self._debug_mode_hint()}"
                "[bold yellow]检测到首次在本项目运行。[/]\n"
                "👉 请在下方输入 [bold green]/init[/] 执行初始化，系统将下载扫描器规则并构建本地代码索引。",
                title="欢迎使用 AutoPatch-J",
                style=SYSTEM_STYLE,
            )
            return

        stats = runtime.symbol_indexer.get_stats() if runtime else {}
        file_count = stats.get("file", 0)
        self.renderer.print_panel(
            f"当前项目: {repo_root}\n"
            f"[bold green][就绪] 已静默加载现有工作台与本地索引 (共包含 {file_count} 个项目文件)。[/]\n"
            f"{self._debug_mode_hint()}"
            "💡 提示：若代码发生大规模变更，请使用 [bold]/reindex[/] 手动刷新 AST 缓存。\n"
            "输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="AutoPatch-J",
            style=SYSTEM_STYLE,
        )

    def _debug_mode_hint(self) -> str:
        if GlobalConfig.debug_mode:
            return "[bold green][调试模式] 显示完整思考链与工具输出详情。[/]\n"
        return ""
