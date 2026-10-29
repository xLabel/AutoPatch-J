"""启动欢迎页及首次初始化操作提示。"""

from __future__ import annotations

import pathlib

from autopatch_j import config
from autopatch_j.cli import render
from autopatch_j.cli import runtime as cli_runtime


class WelcomePresenter:
    """展示启动面板及首次使用提示。"""

    def __init__(self, renderer: render.CliRenderer) -> None:
        self.renderer = renderer

    def render(
        self,
        repo_root: pathlib.Path | None,
        is_first_run: bool,
        runtime: cli_runtime.CliRuntime | None,
    ) -> None:
        """按项目和索引初始化状态展示启动指引，不执行初始化。"""
        if repo_root is None:
            self.renderer.print_panel(
                "AutoPatch-J: Java 安全与正确性修复智能体\n"
                f"{self._debug_mode_hint()}"
                "输入 /help 查看命令，使用 @ 符号绑定上下文。",
                title="AutoPatch-J",
                style=render.SYSTEM_STYLE,
            )
            self.renderer.print_agent_text(
                "未检测到有效目录，请进入项目目录后执行 /init"
            )
            return

        if is_first_run:
            self.renderer.print_panel(
                f"当前项目: {repo_root}\n"
                f"{self._debug_mode_hint()}"
                "[bold yellow]检测到首次在本项目运行。[/]\n"
                "👉 请在下方输入 [bold green]/init[/] 执行"
                "初始化，系统将准备扫描器运行时并构建本地代码索引。",
                title="欢迎使用 AutoPatch-J",
                style=render.SYSTEM_STYLE,
            )
            return

        stats = runtime.symbol_indexer.get_stats() if runtime else {}
        file_count = stats.get("file", 0)
        self.renderer.print_panel(
            f"当前项目: {repo_root}\n"
            f"[bold green][就绪] 已静默加载现有工作台与本地索引"
            f" (共包含 {file_count} 个项目文件)。[/]\n"
            f"{self._debug_mode_hint()}"
            "💡 提示：若代码发生大规模变更，请使用 [bold]/reind"
            "ex[/] 手动刷新 AST 缓存。\n"
            "输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="AutoPatch-J",
            style=render.SYSTEM_STYLE,
        )

    def _debug_mode_hint(self) -> str:
        if config.GLOBAL_CONFIG.debug_mode:
            return "[bold green][调试模式] 显示完整思考链与工具输出详情。[/]\n"
        return ""
