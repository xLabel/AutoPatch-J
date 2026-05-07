from __future__ import annotations

from pathlib import Path

from rich.table import Table
from rich.text import Text

from autopatch_j.cli.render import BODY_STYLE, DECISION_STYLE, MUTED_STYLE, SYSTEM_STYLE, CliRenderer
from autopatch_j.cli.runtime import CliRuntime
from autopatch_j.config import GlobalConfig
from autopatch_j.scanners import DEFAULT_SCANNER_CATALOG


class StatusPresenter:
    """Renders status-oriented command output without mixing it into command control flow."""

    def __init__(self, renderer: CliRenderer) -> None:
        self.renderer = renderer

    def render_project_status(self, runtime: CliRuntime, repo_root: Path | None) -> None:
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", width=15)
        table.add_column("Value", style=BODY_STYLE)

        table.add_row(self._label("项目根目录"), self._value(str(repo_root)))
        table.add_row(self._label("LLM 模型"), self._value(GlobalConfig.llm_model))
        table.add_row(self._label("调试模式"), self._value("开启" if GlobalConfig.debug_mode else "关闭"))

        workspace = runtime.workspace_manager.load()
        pending = workspace.current_patch()
        buffer_status = (
            Text.assemble(
                ("存在待确认补丁", DECISION_STYLE),
                (f" ({pending.file_path})", MUTED_STYLE),
            )
            if pending
            else self._value("空闲")
        )
        table.add_row(self._label("补丁缓冲区"), buffer_status)

        stats = runtime.symbol_indexer.get_stats()
        stats_text = (
            f"文件:{stats.get('file', 0)} | 类:{stats.get('class', 0)} | "
            f"方法:{stats.get('method', 0)} (总计:{stats.get('total', 0)})"
        )
        table.add_row(self._label("符号索引"), self._value(stats_text))
        table.add_row(self._label("符号提取"), self._symbol_extract_status(runtime))

        self.renderer.print_panel(table, title=f"[bold {SYSTEM_STYLE}] 项目状态 [/]", style=SYSTEM_STYLE)

    def render_scanners(self, repo_root: Path | None) -> None:
        table = Table(title="扫描器状态", show_header=True, header_style=f"bold {SYSTEM_STYLE}")
        table.add_column("名称", style=SYSTEM_STYLE, width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("功能简介")

        for scanner in DEFAULT_SCANNER_CATALOG.all():
            meta = scanner.get_meta(repo_root)
            status_text = (
                f"[green]● {meta.status}[/green]"
                if meta.is_implemented
                else f"[dim]● {meta.status}[/dim]"
            )
            table.add_row(meta.name, status_text, meta.version if meta.is_implemented else "-", meta.description)

        self.renderer.console.print(table)

    def _symbol_extract_status(self, runtime: CliRuntime) -> Text:
        symbol_status = runtime.symbol_indexer.fetch_symbol_extract_status()
        if str(symbol_status.get("mode", "full")) != "degraded":
            return self._value("正常")

        status_text = Text("已降级", style=DECISION_STYLE)
        last_error = str(symbol_status.get("last_error") or "")
        if last_error:
            status_text.append(f" ({last_error})", style=MUTED_STYLE)
        return status_text

    def _label(self, text: str) -> Text:
        return Text(text, style=f"bold {SYSTEM_STYLE}")

    def _value(self, text: str, style: str = BODY_STYLE) -> Text:
        return Text(text, style=style)
