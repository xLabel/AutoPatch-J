"""运行配置、真实扫描器及项目索引状态的终端展示。"""

from __future__ import annotations

import pathlib

from rich import table as rich_table
from rich import text as rich_text

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.application import runtime as project_runtime
from autopatch_j.cli import render
from autopatch_j.core.project import symbol_index
from autopatch_j.scanners import catalog
from autopatch_j.scanners import models


class StatusPresenter:
    """展示项目状态，不承载命令业务判断。"""

    def __init__(
        self,
        renderer: render.CliRenderer,
        settings: config.AppConfig,
        scanners: catalog.ScannerCatalog,
    ) -> None:
        self.renderer = renderer
        self.settings = settings
        self.scanners = scanners

    def render_status(
        self,
        runtime: project_runtime.ProjectRuntime | None,
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
            self._value("已配置" if self.settings.llm_api_key else "缺失"),
        )
        table.add_row(
            self._label("LLM Base URL"),
            self._value(self.settings.llm_base_url or "缺失"),
        )
        table.add_row(
            self._label("LLM 模型"),
            self._value(self.settings.llm_model or "缺失"),
        )
        table.add_row(
            self._label("Provider Profile"),
            self._value("Pydantic AI / OpenAI Chat Completions"),
        )
        try:
            profile = self.settings.resolve_llm_context_profile()
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
            self._value("开启" if self.settings.debug_mode else "关闭"),
        )
        table.add_row(
            self._label("单轮模型请求上限"),
            self._value(
                str(self.settings.llm_request_limit)
                if self.settings.llm_request_limit is not None
                else "无限制"
            ),
        )
        table.add_row(
            self._label("LLM 就绪"),
            self._value(self.settings.llm_unavailable_reason() or "配置有效"),
        )
        table.add_row(self._label("Semgrep"), self._scanner_status(repo_root))
        table.add_row(self._label("Tree-sitter"), self._value("ok"))

        if runtime is not None:
            try:
                self._add_workspace_rows(table, runtime)
            # 诊断保留独立配置项，无法读取的项目项明确标为失败。
            except Exception:  # pylint: disable=broad-exception-caught
                table.add_row(
                    self._label("项目状态"),
                    self._value(error_messages.STATE_UNAVAILABLE),
                )

        self.renderer.print_panel(
            table,
            title=f"[bold {render.SYSTEM_STYLE}] 项目状态 [/]",
            style=render.SYSTEM_STYLE,
        )

    def _add_workspace_rows(
        self, table: rich_table.Table, runtime: project_runtime.ProjectRuntime
    ) -> None:
        """向状态表加入持久工作台与符号提取诊断。"""
        state = runtime.repair.store.load()
        table.add_row(self._label("修复任务"), self._value(state.status))
        if state.last_apply_error:
            table.add_row(
                self._label("上次应用失败"), self._value(state.last_apply_error)
            )
        table.add_row(
            self._label("写范围"),
            self._value("、".join(state.write_paths) or "未授权"),
        )

        try:
            stats_text = self._format_symbol_stats(
                runtime.symbol_index.get_stats()
            )
        except symbol_index.IndexNotReadyError as exc:
            stats_text = str(exc)
        table.add_row(self._label("符号索引"), self._value(stats_text))

    def _scanner_status(self, repo_root: pathlib.Path | None) -> rich_text.Text:
        scanner = self.scanners.get("semgrep")
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

        for scanner in self.scanners.all():
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

    def _label(self, text: str) -> rich_text.Text:
        return rich_text.Text(text, style=f"bold {render.SYSTEM_STYLE}")

    def _value(
        self, text: str, style: str = render.BODY_STYLE
    ) -> rich_text.Text:
        return rich_text.Text(text, style=style)
