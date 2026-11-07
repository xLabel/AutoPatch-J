"""启动欢迎页及首次初始化操作提示。"""

from __future__ import annotations

import pathlib

from autopatch_j.application import runtime as project_runtime
from autopatch_j.cli import render
from autopatch_j.core.project import symbol_index


class WelcomePresenter:
    """展示启动面板及首次使用提示。"""

    def __init__(
        self, renderer: render.CliRenderer, *, debug_mode: bool = False
    ) -> None:
        self.renderer = renderer
        self.debug_mode = debug_mode

    def render(
        self,
        repo_root: pathlib.Path | None,
        is_first_run: bool,
        runtime: project_runtime.ProjectRuntime | None,
    ) -> None:
        """按项目和索引初始化状态展示启动指引，不执行初始化。"""
        if runtime is None:
            self.renderer.print_agent_text(
                "项目服务未就绪；输入 /status 查看诊断，/help 查看命令。"
            )
            return
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

        try:
            stats = runtime.symbol_index.get_stats() if runtime else {}
        except symbol_index.IndexNotReadyError as exc:
            self.renderer.print_agent_text(str(exc))
            return
        file_count = stats.get("file", 0)
        self.renderer.print_panel(
            f"当前项目: {repo_root}\n"
            f"[bold green][就绪] 已静默加载现有工作台与本地索引"
            f" (共包含 {file_count} 个项目文件)。[/]\n"
            f"{self._debug_mode_hint()}"
            "💡 提示：代码变更后可使用 [bold]/init[/] 刷新本地索引。\n"
            "输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="AutoPatch-J",
            style=render.SYSTEM_STYLE,
        )

    def _debug_mode_hint(self) -> str:
        if self.debug_mode:
            return "[bold green][调试模式] 显示完整思考链与工具输出详情。[/]\n"
        return ""
