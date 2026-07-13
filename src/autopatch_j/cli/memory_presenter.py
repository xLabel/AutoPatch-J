from __future__ import annotations

from collections.abc import Sequence

from rich.table import Table
from rich.text import Text

from autopatch_j.cli.render import BODY_STYLE, MUTED_STYLE, SYSTEM_STYLE, CliRenderer
from autopatch_j.core.memory import MemoryDetail, MemoryItemSummary, MemoryStatus


class MemoryPresenter:
    """Render typed Memory facade results without leaking control flow into the CLI host."""

    def __init__(self, renderer: CliRenderer, *, show_raw_errors: bool) -> None:
        self.renderer = renderer
        self.show_raw_errors = show_raw_errors

    def render_status(self, status: MemoryStatus) -> None:
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", style=f"bold {SYSTEM_STYLE}", width=20)
        table.add_column("Value", style=BODY_STYLE)
        state = "degraded" if status.degraded else "healthy"
        last_error = "无"
        if status.last_error:
            last_error = (
                status.last_error
                if self.show_raw_errors
                else "已记录（启用 AUTOPATCH_DEBUG=true 查看 RAW 错误）"
            )
        rows = (
            ("状态", state),
            ("数据库", status.db_path),
            ("Schema", status.schema_version),
            ("Generation", status.generation),
            ("Active thread", status.active_thread_id),
            ("Threads", status.thread_count),
            ("Turns", status.turn_count),
            ("Active items", status.active_item_count),
            ("Pending jobs", status.pending_jobs),
            ("Leased jobs", status.leased_jobs),
            ("Retry jobs", status.retry_wait_jobs),
            ("Last success", status.last_succeeded_at or "无"),
            ("Last error", last_error),
        )
        for label, value in rows:
            table.add_row(label, Text(str(value), style=BODY_STYLE))
        self.renderer.print_panel(table, title="Memory 状态", style=SYSTEM_STYLE)

    def render_list(self, items: Sequence[MemoryItemSummary]) -> None:
        if not items:
            self.renderer.print_agent_text("当前没有 active Memory。")
            return
        table = Table(show_header=True, header_style=f"bold {SYSTEM_STYLE}", box=None)
        table.add_column("ID", style=SYSTEM_STYLE)
        table.add_column("类型")
        table.add_column("标题")
        table.add_column("摘要")
        table.add_column("更新时间", style=MUTED_STYLE)
        for item in items:
            table.add_row(
                Text(str(item.id), style=SYSTEM_STYLE),
                Text(str(item.kind), style=BODY_STYLE),
                Text(str(item.title), style=BODY_STYLE),
                Text(str(item.synopsis), style=BODY_STYLE),
                Text(str(item.updated_at), style=MUTED_STYLE),
            )
        self.renderer.print_table(table)

    def render_detail(self, detail: MemoryDetail) -> None:
        table = Table(box=None, show_header=False, padding=(0, 2))
        table.add_column("Key", style=f"bold {SYSTEM_STYLE}", width=18)
        table.add_column("Value", style=BODY_STYLE)
        rows = (
            ("ID", detail.id),
            ("Logical ID", detail.logical_id),
            ("Revision", detail.revision),
            ("类型", detail.kind),
            ("Thread", detail.thread_id or "repo"),
            ("标题", detail.title),
            ("摘要", detail.synopsis),
            ("正文", detail.content),
            ("状态", detail.status),
            ("Non-factual", detail.non_factual),
            ("Access count", detail.access_count),
            ("Last accessed", detail.last_accessed_at or "无"),
        )
        for label, value in rows:
            table.add_row(label, Text(str(value), style=BODY_STYLE))
        self.renderer.print_panel(table, title="Memory 详情", style=SYSTEM_STYLE)

        if not detail.sources:
            return
        sources = Table(show_header=True, header_style=f"bold {SYSTEM_STYLE}", box=None)
        sources.add_column("Turn")
        sources.add_column("Role")
        sources.add_column("原文摘录")
        sources.add_column("时间", style=MUTED_STYLE)
        for source in detail.sources:
            sources.add_row(
                Text(str(source.turn_id), style=BODY_STYLE),
                Text(str(source.role), style=BODY_STYLE),
                Text(str(source.quote), style=BODY_STYLE),
                Text(str(source.created_at), style=MUTED_STYLE),
            )
        self.renderer.print_table(sources)
