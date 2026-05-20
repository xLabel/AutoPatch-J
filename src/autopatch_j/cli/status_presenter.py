from __future__ import annotations

from pathlib import Path

from rich.table import Table
from rich.text import Text

from autopatch_j.cli.render import BODY_STYLE, DECISION_STYLE, MUTED_STYLE, SYSTEM_STYLE, CliRenderer
from autopatch_j.cli.runtime import CliRuntime
from autopatch_j.config import GlobalConfig
from autopatch_j.scanners import DEFAULT_SCANNER_CATALOG
from autopatch_j.scanners.models import ScannerMeta


class StatusPresenter:
    """Renders status-oriented command output without mixing it into command control flow."""

    def __init__(self, renderer: CliRenderer) -> None:
        self.renderer = renderer

    def render_status(self, runtime: CliRuntime | None, repo_root: Path | None) -> None:
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", width=18)
        table.add_column("Value", style=BODY_STYLE)

        table.add_row(self._label("项目根目录"), self._value(str(repo_root) if repo_root else "未检测到"))
        table.add_row(self._label("工作区"), self._value("已初始化" if runtime else "未初始化"))
        table.add_row(self._label("LLM API Key"), self._value("已配置" if GlobalConfig.llm_api_key else "缺失"))
        table.add_row(self._label("LLM Base URL"), self._value(GlobalConfig.llm_base_url or "缺失"))
        table.add_row(self._label("LLM 模型"), self._value(GlobalConfig.llm_model or "缺失"))
        table.add_row(self._label("Stream Dialect"), self._value(GlobalConfig.llm_stream_dialect))
        table.add_row(self._label("Reasoning"), self._value(GlobalConfig.llm_reasoning_effort or "未设置"))
        table.add_row(self._label("Extra Body"), self._value(GlobalConfig.llm_extra_body_error or "ok"))
        table.add_row(self._label("调试模式"), self._value("开启" if GlobalConfig.debug_mode else "关闭"))
        table.add_row(self._label("Semgrep"), self._scanner_status(repo_root))
        table.add_row(self._label("Tree-sitter"), self._value(self._tree_sitter_status()))

        if runtime is not None:
            self._add_workspace_rows(table, runtime)

        self.renderer.print_panel(table, title=f"[bold {SYSTEM_STYLE}] 项目状态 [/]", style=SYSTEM_STYLE)

    def _add_workspace_rows(self, table: Table, runtime: CliRuntime) -> None:
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

    def _scanner_status(self, repo_root: Path | None) -> Text:
        scanner_meta = DEFAULT_SCANNER_CATALOG.get("semgrep").get_meta(repo_root)
        return self._value(f"{scanner_meta.status} - {scanner_meta.reason or scanner_meta.description}")

    def render_scanners(self, repo_root: Path | None) -> None:
        table = Table(title="扫描器状态", show_header=True, header_style=f"bold {SYSTEM_STYLE}")
        table.add_column("名称", style=SYSTEM_STYLE, width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("说明")

        for scanner in DEFAULT_SCANNER_CATALOG.all():
            meta = scanner.get_meta(repo_root)
            table.add_row(
                meta.name,
                self._scanner_status_text(meta),
                meta.version if meta.is_implemented else "-",
                meta.reason or meta.description,
            )

        self.renderer.print_table(table)

    def _scanner_status_text(self, meta: ScannerMeta) -> str:
        if meta.availability == "ready":
            return f"[green]● {meta.status}[/green]"
        if meta.availability == "planned":
            return f"[dim]● {meta.status}[/dim]"
        if meta.availability == "unavailable":
            return f"[yellow]● {meta.status}[/yellow]"
        return f"[dim]● {meta.status}[/dim]"

    def _symbol_extract_status(self, runtime: CliRuntime) -> Text:
        symbol_status = runtime.symbol_indexer.fetch_symbol_extract_status()
        if str(symbol_status.get("mode", "full")) != "degraded":
            return self._value("正常")

        status_text = Text("已降级", style=DECISION_STYLE)
        last_error = str(symbol_status.get("last_error") or "")
        if last_error:
            status_text.append(f" ({last_error})", style=MUTED_STYLE)
        return status_text

    def _tree_sitter_status(self) -> str:
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_java  # noqa: F401
        except ImportError as exc:
            return f"缺失 ({exc.name})"
        return "ok"

    def _label(self, text: str) -> Text:
        return Text(text, style=f"bold {SYSTEM_STYLE}")

    def _value(self, text: str, style: str = BODY_STYLE) -> Text:
        return Text(text, style=style)
