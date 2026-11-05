"""Rich 终端样式、状态提示及人工审核面板。"""

from __future__ import annotations

import re

from rich import box
from rich import console
from rich import panel
from rich import table as rich_table
from rich import text

SYSTEM_STYLE = "#4F8CFF"
DECISION_STYLE = "#EAB308"
SUCCESS_STYLE = "#22C55E"
ERROR_STYLE = "#EF4444"
MUTED_STYLE = "#94A3B8"
BODY_STYLE = "#E5E7EB"


class CliRenderer:
    """Rich 终端输出的统一封装。

    职责边界：
    1. 提供面板、步骤、工具状态、diff、补丁操作面板等高语义输出。
    2. 统一颜色和样式，避免业务层直接拼 Rich 细节。
    3. 不持有业务状态，也不决定何时进入某个工作流。
    """

    def __init__(self) -> None:
        self.console = console.Console()

    def print_plain(self, message: str, end: str = "\n") -> None:
        """按文字显示内容，禁用 Rich 标记解释和高亮。"""
        self.console.print(
            message, end=end, highlight=False, markup=False, style=BODY_STYLE
        )

    def print_user_anchor(self, message: str) -> None:
        """显示用户问答标记。"""
        self.console.print(f"[{MUTED_STYLE}]你: {message}[/]")

    def print_assistant_anchor(self, label: str = "AutoPatch-J") -> None:
        """显示助手问答标记。"""
        self.console.print(f"[{BODY_STYLE}]{label}:[/]")

    def print_panel(
        self,
        content: console.RenderableType,
        title: str | None = None,
        style: str = SYSTEM_STYLE,
    ) -> None:
        """以统一边框显示 Rich 内容，不改变业务状态。"""
        self.console.print(
            panel.Panel(
                content, title=title, border_style=style, box=box.ROUNDED
            )
        )

    def print_table(self, table: rich_table.Table) -> None:
        """显示已构造的 Rich 表格。"""
        self.console.print(table)

    def print_heading(self, message: str) -> None:
        """显示统一样式的标题。"""
        self.console.print(f"[{BODY_STYLE}]{message}[/]")

    def print_blank(self) -> None:
        """补充一个展示空行。"""
        self.console.print()

    def print_step(self, message: str) -> None:
        """显示工作步骤提示，不表示步骤已经完成。"""
        self.console.print(f"> [{MUTED_STYLE}]{message}[/]")

    def print_tool_start(self, tool_name: str, caller: str) -> None:
        """显示指定调用方开始执行工具。"""
        caller_upper = caller.upper()
        style = MUTED_STYLE
        self.console.print(
            f"[{style}]正在执行工具[{caller_upper}]: {tool_name}...[/]"
        )

    def print_agent_text(self, message: str, end: str = "\n") -> None:
        """按文字展示答案或观察，保留调用方指定的行尾。"""
        self.console.print(
            message, end=end, highlight=False, markup=False, style=MUTED_STYLE
        )

    def print_reasoning_text(self, message: str, end: str = "") -> None:
        """按文字追加推理事件，不把 Rich 标记作为样式执行。"""
        self.console.print(
            message,
            end=end,
            highlight=False,
            markup=False,
            style=f"italic {MUTED_STYLE}",
        )

    def print_reasoning_status(self, step: int) -> None:
        """显示折叠推理提示；step 不作为模型完成证据。"""
        del step
        self.console.print(
            f"[italic {MUTED_STYLE}]思考中...[/]",
            end="",
            soft_wrap=True,
        )

    def finish_reasoning_status(self) -> None:
        """收束折叠推理提示所在行。"""
        self.console.print()

    def print_success(self, message: str) -> None:
        """显示调用方已确认的成功结果。"""
        self.console.print(f"[bold {SUCCESS_STYLE}]{message}[/]")

    def print_error(self, message: str) -> None:
        """按文字显示错误，避免错误正文中的 Rich 标记被解释。"""
        self.console.print(
            message,
            highlight=False,
            markup=False,
            style=f"bold {ERROR_STYLE}",
        )

    def print_diff(self, diff: str, title: str = "预览") -> None:
        """渲染带语法高亮的补丁差异"""
        if not diff.strip():
            return

        diff_text = text.Text()
        for line in diff.splitlines(keepends=True):
            if line.startswith("+++") or line.startswith("---"):
                diff_text.append(line, style="bold")
            elif line.startswith("+"):
                diff_text.append(line, style=SUCCESS_STYLE)
            elif line.startswith("-"):
                diff_text.append(line, style=ERROR_STYLE)
            elif line.startswith("@@"):

                match = re.match(
                    r"(@@\s+)(-[0-9,]+)(\s+)(\+[0-9,]+)(\s+@@.*)",
                    line,
                    re.DOTALL,
                )
                if match:
                    diff_text.append(match.group(1), style=SYSTEM_STYLE)
                    diff_text.append(match.group(2), style=ERROR_STYLE)
                    diff_text.append(match.group(3), style=SYSTEM_STYLE)
                    diff_text.append(match.group(4), style=SUCCESS_STYLE)
                    diff_text.append(match.group(5), style=SYSTEM_STYLE)
                else:
                    diff_text.append(line, style=SYSTEM_STYLE)
            else:
                diff_text.append(line)

        self.print_panel(diff_text, title=title, style=DECISION_STYLE)
