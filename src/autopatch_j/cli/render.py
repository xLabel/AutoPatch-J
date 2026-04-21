from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.markdown import Markdown
from rich.live import Live
from rich.text import Text


class CliRenderer:
    """
    终端渲染器 (Presentation Layer)
    职责：使用 rich 库提供高质量的视觉反馈。
    """

    def __init__(self) -> None:
        self.console = Console()

    def print(self, message: object = "", end: str = "\n", style: str | None = None) -> None:
        """通用打印，支持自动 Markdown 渲染"""
        if isinstance(message, str) and style is None:
            if "\n" in message or "```" in message:
                self.console.print(Markdown(message), end=end)
            else:
                self.console.print(message, end=end)
        else:
            self.console.print(message, end=end, style=style)

    def print_panel(self, text: str, title: str | None = None, style: str = "blue") -> None:
        """打印带标题的面板"""
        self.console.print(Panel(text, title=title, border_style=style, padding=(1, 2)))

    def print_diff(self, diff: str, title: str = "补丁预览") -> None:
        """打印标准 Unified Diff 语法高亮"""
        syntax = Syntax(diff, "diff", theme="monokai", background_color="default")
        self.console.print(Panel(syntax, title=title, border_style="yellow"))

    def print_error(self, message: str) -> None:
        """打印错误信息"""
        self.console.print(f"[bold red]✘ 错误:[/bold red] {message}")

    def print_success(self, message: str) -> None:
        """打印成功信息"""
        self.console.print(f"[bold green]✔ 成功:[/bold green] {message}")

    def print_info(self, message: str) -> None:
        """打印提示信息"""
        self.console.print(f"[bold cyan]ℹ 提示:[/bold cyan] {message}")

    def print_action_panel(self, file_path: str, diff: str, validation: str, rationale: str) -> None:
        """渲染补丁审核的浮动面板，包含增删统计和操作指南"""
        # 统计增删行数
        lines = diff.splitlines()
        added = sum(1 for line in lines if line.startswith('+') and not line.startswith('+++'))
        removed = sum(1 for line in lines if line.startswith('-') and not line.startswith('---'))

        # 头部：文件与统计
        header = Text.assemble(
            ("文件: ", "bold cyan"), f"{file_path}  ",
            ("统计: ", "bold cyan"), (f"+{added}行 ", "bold green"), (f"-{removed}行 ", "bold red"),
            (" 校验: ", "bold cyan"), (f"{validation}", "bold yellow" if validation != "ok" else "bold green")
        )

        # 中部：意图说明
        intent = Text.assemble(
            ("\n意图: ", "bold cyan"), (f"{rationale}", "italic")
        )

        # 底部：快捷指令说明
        actions = Text.assemble(
            ("\n" + "─" * 40, "dim"),
            ("\napply  ", "bold green"), " 🚀 应用此补丁并执行三级语义校验",
            ("\ndiscard", "bold red"), " 🗑️ 丢弃此草案并清理缓冲区",
            ("\n<文本> ", "bold blue"), " 💬 直接输入反馈让 Agent 重新生成"
        )

        self.console.print(Panel(
            Text.combine(header, intent, actions),
            title="[bold yellow] 补丁待审核 (PENDING) [/]",
            border_style="yellow",
            padding=(1, 2),
            expand=False
        ))
