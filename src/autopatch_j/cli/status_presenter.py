"""运行配置、真实扫描器及项目索引状态的终端展示。"""

from __future__ import annotations

import importlib
import pathlib

from rich import table as rich_table
from rich import text as rich_text

from autopatch_j import config
from autopatch_j.cli import render
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.scanners import catalog
from autopatch_j.scanners import models


class StatusPresenter:
    """展示项目状态，不承载命令业务判断。"""

    def __init__(self, renderer: render.CliRenderer) -> None:
        self.renderer = renderer

    def render_status(
        self,
        runtime: cli_runtime.CliRuntime | None,
        repo_root: pathlib.Path | None,
    ) -> None:
        """展示配置、工作台与本地依赖状态，不发起模型调用。"""
        table = rich_table.Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", width=18)
        table.add_column("Value", style=render.BODY_STYLE)

        table.add_row(
            self._label("项目根目录"),
            self._value(str(repo_root) if repo_root else "未检测到"),
        )
        table.add_row(
            self._label("工作区"),
            self._value("已初始化" if runtime else "未初始化"),
        )
        table.add_row(
            self._label("LLM API Key"),
            self._value(
                "已配置" if config.GLOBAL_CONFIG.llm_api_key else "缺失"
            ),
        )
        table.add_row(
            self._label("LLM Base URL"),
            self._value(config.GLOBAL_CONFIG.llm_base_url or "缺失"),
        )
        table.add_row(
            self._label("LLM 模型"),
            self._value(config.GLOBAL_CONFIG.llm_model or "缺失"),
        )
        table.add_row(
            self._label("Provider Profile"),
            self._value("Pydantic AI / OpenAI Chat Completions"),
        )
        try:
            profile = config.GLOBAL_CONFIG.resolve_llm_context_profile()
        except ValueError as exc:
            table.add_row(self._label("LLM 容量"), self._value(str(exc)))
        else:
            table.add_row(
                self._label("LLM Input Cap"),
                self._value(f"{profile.input_capacity:,} tokens"),
            )
            table.add_row(
                self._label("LLM Output Cap"),
                self._value(f"{profile.max_output_tokens:,} tokens"),
            )
        table.add_row(
            self._label("调试模式"),
            self._value("开启" if config.GLOBAL_CONFIG.debug_mode else "关闭"),
        )
        table.add_row(self._label("Semgrep"), self._scanner_status(repo_root))
        table.add_row(
            self._label("Tree-sitter"), self._value(self._tree_sitter_status())
        )

        if runtime is not None:
            self._add_workspace_rows(table, runtime)

        self.renderer.print_panel(
            table,
            title=f"[bold {render.SYSTEM_STYLE}] 项目状态 [/]",
            style=render.SYSTEM_STYLE,
        )

    def _add_workspace_rows(
        self, table: rich_table.Table, runtime: cli_runtime.CliRuntime
    ) -> None:
        """向状态表加入持久工作台与符号提取诊断。"""
        workspace = runtime.workspace_manager.load()
        pending = workspace.current_patch()
        buffer_status = (
            rich_text.Text.assemble(
                ("存在待确认补丁", render.DECISION_STYLE),
                (f" ({pending.file_path})", render.MUTED_STYLE),
            )
            if pending
            else self._value("空闲")
        )
        table.add_row(self._label("补丁缓冲区"), buffer_status)

        stats = runtime.symbol_indexer.get_stats()
        stats_text = self._format_symbol_stats(stats)
        table.add_row(self._label("符号索引"), self._value(stats_text))
        table.add_row(
            self._label("符号提取"), self._symbol_extract_status(runtime)
        )

    def _scanner_status(self, repo_root: pathlib.Path | None) -> rich_text.Text:
        scanner = catalog.DEFAULT_SCANNER_CATALOG.get("semgrep")
        if scanner is None:
            return self._value("未注册")
        scanner_meta = scanner.get_meta(repo_root)
        return self._value(
            f"{scanner_meta.status}"
            f" - {scanner_meta.reason or scanner_meta.description}"
        )

    def render_scanners(self, repo_root: pathlib.Path | None) -> None:
        """展示扫描器目录中实际实现的运行状态。"""
        table = rich_table.Table(
            title="当前扫描器",
            show_header=True,
            header_style=f"bold {render.SYSTEM_STYLE}",
        )
        table.add_column("名称", style=render.SYSTEM_STYLE, width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("说明")

        for scanner in catalog.DEFAULT_SCANNER_CATALOG.implemented():
            meta = scanner.get_meta(repo_root)
            table.add_row(
                self._scanner_label(meta.name.value),
                self._scanner_status_text(meta),
                meta.version,
                meta.reason or meta.description,
            )

        self.renderer.print_table(table)

    def _scanner_label(self, scanner_name: str) -> str:
        return {
            "semgrep": "Semgrep",
            "spotbugs": "SpotBugs",
            "pmd": "PMD",
            "checkstyle": "Checkstyle",
        }.get(scanner_name, scanner_name)

    def _scanner_status_text(self, meta: models.ScannerMeta) -> str:
        if meta.availability == "ready":
            return f"[green]● {meta.status}[/green]"
        if meta.availability == "unavailable":
            return f"[yellow]● {meta.status}[/yellow]"
        return f"[dim]● {meta.status}[/dim]"

    def _symbol_extract_status(
        self, runtime: cli_runtime.CliRuntime
    ) -> rich_text.Text:
        symbol_status = runtime.symbol_indexer.fetch_symbol_extract_status()
        if symbol_status["mode"] != "degraded":
            return self._value("正常")

        status_text = rich_text.Text("已降级", style=render.DECISION_STYLE)
        last_error = symbol_status["last_error"] or ""
        if last_error:
            status_text.append(f" ({last_error})", style=render.MUTED_STYLE)
        return status_text

    def _format_symbol_stats(self, stats: dict[str, int]) -> str:
        """将索引计数格式化为终端诊断文本。"""
        ordered_kinds = (
            "file",
            "class",
            "interface",
            "enum",
            "record",
            "constructor",
            "method",
        )
        labels = {
            "file": "文件",
            "class": "类",
            "interface": "接口",
            "enum": "枚举",
            "record": "record",
            "constructor": "构造器",
            "method": "方法",
        }
        parts = [
            f"{labels[kind]}:{stats.get(kind, 0)}"
            for kind in ordered_kinds
            if stats.get(kind, 0)
        ]
        if not parts:
            parts = ["文件:0"]
        return " | ".join(parts) + f" (总计:{stats.get("total", 0)})"

    def _tree_sitter_status(self) -> str:
        try:
            for module_name in ("tree_sitter", "tree_sitter_java"):
                importlib.import_module(module_name)
        except ImportError as exc:
            return f"缺失 ({exc.name})"
        return "ok"

    def _label(self, text: str) -> rich_text.Text:
        return rich_text.Text(text, style=f"bold {render.SYSTEM_STYLE}")

    def _value(
        self, text: str, style: str = render.BODY_STYLE
    ) -> rich_text.Text:
        return rich_text.Text(text, style=style)
