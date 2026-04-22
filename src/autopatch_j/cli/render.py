from __future__ import annotations

from typing import Any
from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table
from rich.box import ROUNDED


class CliRenderer:
    """
    CLI 视觉渲染引擎 (Presentation Layer)
    职责：封装 Rich 组件，提供统一的、高语义化的 UI 输出。
    """

    def __init__(self) -> None:
        self.console = Console()

    def print(self, *args, **kwargs) -> None:
        self.console.print(*args, **kwargs)

    def print_panel(self, content: Any, title: str | None = None, style: str = "cyan") -> None:
        self.console.print(Panel(content, title=title, border_style=style, box=ROUNDED))

    def print_step(self, message: str) -> None:
        self.console.print(f"> [dim]{message}[/dim]")

    def print_success(self, message: str) -> None:
        # 🚀 视觉加固：前缀与内容颜色统一
        self.console.print(f"[bold green]成功: {message}[/bold green]")

    def print_error(self, message: str) -> None:
        # 🚀 视觉加固：前缀与内容颜色统一
        self.console.print(f"[bold red]错误: {message}[/bold red]")

    def print_info(self, message: str) -> None:
        # 🚀 视觉加固：前缀与内容颜色统一
        self.console.print(f"[bold cyan]提示: {message}[/bold cyan]")

    def print_diff(self, diff: str, title: str = "预览") -> None:
        """渲染带语法高亮的补丁差异"""
        if not diff.strip():
            return

        # 🚀 极致视觉对齐：放弃自带固定色差的主题，改用物理解析
        # 确保 "添加行" 与 "删除行" 的颜色与底层统计面板的 "green" / "red" 100% 绝对一致
        diff_text = Text()
        for line in diff.splitlines(keepends=True):
            if line.startswith("+++") or line.startswith("---"):
                diff_text.append(line, style="bold")
            elif line.startswith("+"):
                diff_text.append(line, style="green")
            elif line.startswith("-"):
                diff_text.append(line, style="red")
            elif line.startswith("@@"):
                import re
                match = re.match(r'(@@\s+)(-[0-9,]+)(\s+)(\+[0-9,]+)(\s+@@.*)', line, re.DOTALL)
                if match:
                    diff_text.append(match.group(1), style="cyan")
                    diff_text.append(match.group(2), style="red")
                    diff_text.append(match.group(3), style="cyan")
                    diff_text.append(match.group(4), style="green")
                    diff_text.append(match.group(5), style="cyan")
                else:
                    diff_text.append(line, style="cyan")
            else:
                diff_text.append(line)

        self.print_panel(diff_text, title=title, style="yellow")

    def print_action_panel(self, file_path: str, diff: str, validation: str, rationale: str, current_idx: int = 1, total_count: int = 1) -> None:
        """展示补丁审核决策面板"""
        # 计算统计
        add_lines = diff.count("\n+") - diff.count("\n+++")
        del_lines = diff.count("\n-") - diff.count("\n---")
        
        # 🚀 视觉对齐：统计信息使用鲜明的绿红配色
        stats = Text.assemble(
            ("文件: ", "dim"), (file_path, "bold cyan"),
            ("  统计: ", "dim"), (f"+{add_lines}行", "green"), (" ", ""), (f"-{del_lines}行", "red"),
            ("  校验: ", "dim"), (validation, "green" if validation == "ok" else "yellow")
        )

        rationale_text = Text(rationale, style="italic")
        
        # 组装文本页眉
        header = Text.assemble(
            "\n", stats, "\n",
            ("意图: ", "bold"), rationale_text, "\n",
            ("─" * 40, "dim"), "\n"
        )
        
        # 组装操作指南表格
        guide = Table.grid(padding=(0, 1))
        guide.add_column(style="bold yellow")
        guide.add_column()
        guide.add_row("apply", "  > 应用此补丁并执行三级验证")
        guide.add_row("discard", "  > 丢弃此草案并进入下一个")
        guide.add_row("<文本>", "  > 直接输入反馈让 Agent 重新生成")

        # 使用 Group 组合
        content = Group(
            header,
            guide,
            Text("\n")
        )

        # 🚀 视觉对齐：保持黄色主基调
        title = f"补丁待审核 (PENDING) [{current_idx}/{total_count}]" if total_count > 1 else "补丁待审核 (PENDING)"
        self.print_panel(content, title=title, style="yellow")

    def print_no_issue_panel(self, scope_paths: list[str], scanner_summary: str, llm_summary: str) -> None:
        """展示‘未发现问题’的固定三段式结论卡片"""
        scope_header = Text("检查范围", style="dim")
        scope_body = Text()
        for i, path in enumerate(scope_paths):
            scope_body.append(path, style="bold cyan")
            if i < len(scope_paths) - 1:
                scope_body.append("\n")
        scanner = Text.assemble(
            ("静态扫描器结论: ", "dim"),
            (scanner_summary, "green"),
        )
        llm = Text.assemble(
            ("LLM 复核结论: ", "dim"),
            (llm_summary, "green"),
        )

        content = Group(
            Text("\n"),
            scope_header,
            scope_body,
            Text("─" * 56, style="dim"),
            scanner,
            Text("─" * 56, style="dim"),
            llm,
            Text("\n"),
        )
        self.print_panel(content, title="检查结论", style="green")
