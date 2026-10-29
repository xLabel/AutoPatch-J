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

    def print_action_panel(
        self,
        file_path: str,
        diff: str,
        validation: str,
        rationale: str,
        current_idx: int = 1,
        total_count: int = 1,
        source_hint: str | None = None,
        blocking_error: str | None = None,
    ) -> None:
        """展示补丁确认决策面板"""
        # 计算统计
        add_lines = diff.count("\n+") - diff.count("\n+++")
        del_lines = diff.count("\n-") - diff.count("\n---")

        stats = text.Text.assemble(
            ("文件: ", MUTED_STYLE),
            (file_path, f"bold {SYSTEM_STYLE}"),
            ("  统计: ", MUTED_STYLE),
            (f"+{add_lines}行", SUCCESS_STYLE),
            (" ", ""),
            (f"-{del_lines}行", ERROR_STYLE),
            ("  语法: ", MUTED_STYLE),
            (
                validation,
                SUCCESS_STYLE if validation == "ok" else DECISION_STYLE,
            ),
        )

        rationale_text = text.Text(rationale, style="italic")
        source_line = text.Text()
        if source_hint:
            source_line = text.Text.assemble(
                ("来源: ", MUTED_STYLE),
                (source_hint, BODY_STYLE),
                ("\n", ""),
            )
        blocking_line = text.Text()
        if blocking_error:
            blocking_line = text.Text.assemble(
                ("阻断原因: ", f"bold {ERROR_STYLE}"),
                (blocking_error, ERROR_STYLE),
                ("\n", ""),
            )

        # 组装文本页眉
        header = text.Text.assemble(
            "\n",
            stats,
            "\n",
            source_line,
            blocking_line,
            ("意图: ", "bold"),
            rationale_text,
            "\n",
            ("─" * 40, MUTED_STYLE),
            "\n",
        )

        # 组装操作指南表格
        guide = rich_table.Table.grid(padding=(0, 1))
        guide.add_column(style=f"bold {DECISION_STYLE}")
        guide.add_column()
        if blocking_error:
            guide.add_row("discard", "  > 丢弃此草案并进入下一个")
            guide.add_row("abort", "  > 中止审核流程，随后重新扫描")
        else:
            guide.add_row("apply", "  > 应用此补丁并复扫目标 finding")
            guide.add_row("discard", "  > 丢弃此草案并进入下一个")
            guide.add_row("abort", "  > 中止审核流程并清空队列")
            guide.add_row("<文本>", "  > 直接输入反馈让 Agent 重新生成")

        # 使用 Group 组合
        content = console.Group(header, guide, text.Text("\n"))

        state = "STALE" if blocking_error else "PENDING"
        title = (
            f"待确认补丁 ({state}) [{current_idx}/{total_count}]"
            if total_count > 1
            else f"待确认补丁 ({state})"
        )
        self.print_panel(content, title=title, style=DECISION_STYLE)

    def print_no_issue_panel(
        self, scope_paths: list[str], scanner_summary: str, llm_summary: str
    ) -> None:
        """展示‘未发现问题’的固定三段式结论卡片"""
        scope_header = text.Text("检查范围", style=MUTED_STYLE)
        scope_body = text.Text()
        for i, path in enumerate(scope_paths):
            scope_body.append(path, style=f"bold {SYSTEM_STYLE}")
            if i < len(scope_paths) - 1:
                scope_body.append("\n")
        scanner = text.Text.assemble(
            ("静态扫描器结论: ", MUTED_STYLE),
            (scanner_summary, SUCCESS_STYLE),
        )
        llm = text.Text.assemble(
            ("LLM 复核结论: ", MUTED_STYLE),
            (llm_summary, SUCCESS_STYLE),
        )

        content = console.Group(
            text.Text("\n"),
            scope_header,
            scope_body,
            text.Text("─" * 56, style=MUTED_STYLE),
            scanner,
            text.Text("─" * 56, style=MUTED_STYLE),
            llm,
            text.Text("\n"),
        )
        self.print_panel(content, title="检查结果", style=SUCCESS_STYLE)
