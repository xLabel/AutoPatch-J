from __future__ import annotations

from typing import Any
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.box import ROUNDED

SYSTEM_STYLE = "#4F8CFF"
DECISION_STYLE = "#EAB308"
SUCCESS_STYLE = "#22C55E"
ERROR_STYLE = "#EF4444"
MUTED_STYLE = "#94A3B8"
BODY_STYLE = "#E5E7EB"


class CliRenderer:
    """
    CLI 视觉渲染引擎 (Presentation Layer)
    职责：封装 Rich 组件，提供统一的、高语义化的 UI 输出。
    """

    def __init__(self) -> None:
        self.console = Console()

    def print(self, *args, **kwargs) -> None:
        self.console.print(*args, **kwargs)

    def print_plain(self, message: str, end: str = "\n") -> None:
        self.console.print(message, end=end, highlight=False, markup=False, style=BODY_STYLE)

    def print_user_anchor(self, message: str) -> None:
        self.console.print(f"[{MUTED_STYLE}]你: {message}[/]")

    def print_assistant_anchor(self, label: str = "AutoPatch-J") -> None:
        self.console.print(f"[bold {SYSTEM_STYLE}]{label}:[/]")

    def print_panel(self, content: Any, title: str | None = None, style: str = SYSTEM_STYLE) -> None:
        self.console.print(Panel(content, title=title, border_style=style, box=ROUNDED))

    def print_step(self, message: str) -> None:
        self.console.print(f"> [{MUTED_STYLE}]{message}[/]")

    def print_tool_start(self, tool_name: str, caller: str) -> None:
        caller_upper = caller.upper()
        style = f"bold {SYSTEM_STYLE}" if caller_upper == "AGENT" else f"bold {MUTED_STYLE}"
        self.console.print(f"\n[{style}]正在执行工具[{caller_upper}]: {tool_name}...[/]")

    def print_reasoning(self, message: str, end: str = "") -> None:
        self.console.print(message, end=end, style=f"italic {MUTED_STYLE}")

    def print_reasoning_status(self, step: int) -> None:
        dots = "." * ((step % 3) + 1)
        self.console.print(
            f"\r[italic {MUTED_STYLE}]思考中{dots}[/]",
            end="",
            soft_wrap=True,
        )

    def finish_reasoning_status(self) -> None:
        self.console.print()

    def print_observation(self, message: str) -> None:
        self.console.print(f"\n{message}\n", style=BODY_STYLE)

    def print_success(self, message: str) -> None:
        self.console.print(f"[bold {SUCCESS_STYLE}]{message}[/]")

    def print_error(self, message: str) -> None:
        self.console.print(f"[bold {ERROR_STYLE}]{message}[/]")

    def print_info(self, message: str) -> None:
        self.console.print(f"[bold {MUTED_STYLE}]{message}[/]")

    def print_diff(self, diff: str, title: str = "预览") -> None:
        """渲染带语法高亮的补丁差异"""
        if not diff.strip():
            return

        diff_text = Text()
        for line in diff.splitlines(keepends=True):
            if line.startswith("+++") or line.startswith("---"):
                diff_text.append(line, style="bold")
            elif line.startswith("+"):
                diff_text.append(line, style=SUCCESS_STYLE)
            elif line.startswith("-"):
                diff_text.append(line, style=ERROR_STYLE)
            elif line.startswith("@@"):
                import re
                match = re.match(r'(@@\s+)(-[0-9,]+)(\s+)(\+[0-9,]+)(\s+@@.*)', line, re.DOTALL)
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
    ) -> None:
        """展示补丁确认决策面板"""
        # 计算统计
        add_lines = diff.count("\n+") - diff.count("\n+++")
        del_lines = diff.count("\n-") - diff.count("\n---")
        
        stats = Text.assemble(
            ("文件: ", MUTED_STYLE),
            (file_path, f"bold {SYSTEM_STYLE}"),
            ("  统计: ", MUTED_STYLE),
            (f"+{add_lines}行", SUCCESS_STYLE),
            (" ", ""),
            (f"-{del_lines}行", ERROR_STYLE),
            ("  校验: ", MUTED_STYLE),
            (validation, SUCCESS_STYLE if validation == "ok" else DECISION_STYLE),
        )

        rationale_text = Text(rationale, style="italic")
        source_line = Text()
        if source_hint:
            source_line = Text.assemble(
                ("来源: ", MUTED_STYLE),
                (source_hint, BODY_STYLE),
                ("\n", ""),
            )
        
        # 组装文本页眉
        header = Text.assemble(
            "\n", stats, "\n",
            source_line,
            ("意图: ", "bold"), rationale_text, "\n",
            ("─" * 40, MUTED_STYLE), "\n"
        )
        
        # 组装操作指南表格
        guide = Table.grid(padding=(0, 1))
        guide.add_column(style=f"bold {DECISION_STYLE}")
        guide.add_column()
        guide.add_row("apply", "  > 应用此补丁并执行三级验证")
        guide.add_row("discard", "  > 丢弃此草案并进入下一个")
        guide.add_row("abort", "  > 中止审核流程并清空队列")
        guide.add_row("<文本>", "  > 直接输入反馈让 Agent 重新生成")

        # 使用 Group 组合
        content = Group(
            header,
            guide,
            Text("\n")
        )

        title = f"待确认补丁 (PENDING) [{current_idx}/{total_count}]" if total_count > 1 else "待确认补丁 (PENDING)"
        self.print_panel(content, title=title, style=DECISION_STYLE)

    def print_no_issue_panel(self, scope_paths: list[str], scanner_summary: str, llm_summary: str) -> None:
        """展示‘未发现问题’的固定三段式结论卡片"""
        scope_header = Text("检查范围", style=MUTED_STYLE)
        scope_body = Text()
        for i, path in enumerate(scope_paths):
            scope_body.append(path, style=f"bold {SYSTEM_STYLE}")
            if i < len(scope_paths) - 1:
                scope_body.append("\n")
        scanner = Text.assemble(
            ("静态扫描器结论: ", MUTED_STYLE),
            (scanner_summary, SUCCESS_STYLE),
        )
        llm = Text.assemble(
            ("LLM 复核结论: ", MUTED_STYLE),
            (llm_summary, SUCCESS_STYLE),
        )

        content = Group(
            Text("\n"),
            scope_header,
            scope_body,
            Text("─" * 56, style=MUTED_STYLE),
            scanner,
            Text("─" * 56, style=MUTED_STYLE),
            llm,
            Text("\n"),
        )
        self.print_panel(content, title="检查结果", style=SUCCESS_STYLE)
