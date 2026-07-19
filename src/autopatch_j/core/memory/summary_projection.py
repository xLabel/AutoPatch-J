from __future__ import annotations

import hashlib
import html
import os
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .constants import MAX_READ_SOURCES, MAX_SOURCE_EXCERPT_CHARS
from .models import (
    MemoryDetail,
    MemorySummaryRefreshResult,
    MemorySummarySnapshot,
    MemorySummaryStatus,
)
from .text_utils import compact_text


MEMORY_SUMMARY_HEADER = (
    "<!-- 自动生成，仅供人类审阅；不参与 Memory 处理或 LLM 上下文，"
    "Memory 以 memory.db 为准。 -->"
)


class MemorySummaryProjector:
    """Render the human review projection without making it a runtime read source."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self.path = Path(path)
        self._clock = clock
        self._last_semantic_digest: str | None = None
        self._last_file_digest: str | None = None
        self._last_projected_at: str | None = None
        self._last_item_count = 0
        self._current = False
        self._last_error = ""

    def refresh(
        self,
        snapshot: MemorySummarySnapshot,
        *,
        force: bool = False,
    ) -> MemorySummaryRefreshResult:
        semantic_digest = self._digest_text(repr(snapshot))
        current_file_digest = self._file_digest()
        if (
            not force
            and self._current
            and semantic_digest == self._last_semantic_digest
            and current_file_digest == self._last_file_digest
        ):
            return MemorySummaryRefreshResult(status=self.status(), changed=False)

        projected_at = self._now_iso()
        payload = self.render(snapshot, projected_at=projected_at)
        payload_digest = self._digest_text(payload)
        self._write_atomic(payload)
        self._last_semantic_digest = semantic_digest
        self._last_file_digest = payload_digest
        self._last_projected_at = projected_at
        self._last_item_count = len(snapshot.items)
        self._current = True
        self._last_error = ""
        return MemorySummaryRefreshResult(status=self.status(), changed=True)

    def mark_stale(self, error: str) -> MemorySummaryStatus:
        self._current = False
        self._last_error = " ".join(str(error).split())[:500]
        return self.status()

    def status(self) -> MemorySummaryStatus:
        state = "missing"
        if self.path.exists():
            state = "current" if self._is_current_file() else "stale"
        return MemorySummaryStatus(
            path=self.path,
            state=state,
            active_item_count=self._last_item_count,
            last_projected_at=self._projected_at_or_mtime(),
            last_error=self._last_error,
        )

    @staticmethod
    def render(snapshot: MemorySummarySnapshot, *, projected_at: str) -> str:
        lines = [
            MEMORY_SUMMARY_HEADER,
            "",
            "# AutoPatch-J Memory Summary",
            "",
            f"- 最近投影：{_escape_inline(projected_at)}",
            f"- Active thread：`{_escape_inline(snapshot.active_thread_id)}`",
            f"- Active items：{len(snapshot.items)}",
            "",
            "## 当前 thread checkpoint（有损）",
            "",
            "> 这是随当前讨论变化的有损工作记忆，不等同于项目长期 Memory。",
            "",
            _quote_block(snapshot.thread_checkpoint or "暂无 checkpoint。"),
        ]

        groups = (
            ("project_decision", "Project decisions"),
            ("user_preference", "User preferences"),
            ("discussion_context", "Current thread context"),
        )
        for kind, title in groups:
            items = tuple(item for item in snapshot.items if item.kind == kind)
            lines.extend(("", f"## {title}（{len(items)}）", ""))
            if not items:
                lines.append("暂无。")
                continue
            for item in items:
                lines.extend(_render_item(item))

        return "\n".join(lines).rstrip() + "\n"

    def _write_atomic(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(
            f".{self.path.name}.{uuid4().hex}.tmp"
        )
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            os.replace(temp_path, self.path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _is_current_file(self) -> bool:
        if not self._current or self._last_file_digest is None:
            return False
        return self._file_digest() == self._last_file_digest

    def _file_digest(self) -> str | None:
        try:
            return hashlib.sha256(self.path.read_bytes()).hexdigest()
        except FileNotFoundError:
            return None
        except OSError:
            return ""

    def _projected_at_or_mtime(self) -> str | None:
        if self._last_projected_at is not None:
            return self._last_projected_at
        try:
            value = datetime.fromtimestamp(
                self.path.stat().st_mtime,
                tz=timezone.utc,
            )
        except OSError:
            return None
        return value.isoformat(timespec="seconds")

    def _now_iso(self) -> str:
        value = self._clock()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _digest_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _render_item(item: MemoryDetail) -> list[str]:
    lines = [
        f"### {_escape_inline(item.subject)}",
        "",
        "**陈述**",
        "",
        _quote_block(item.statement),
        "",
        "**内容**",
        "",
        _quote_block(item.content),
        "",
        f"- 类型：`{_escape_inline(item.kind)}`",
        f"- 适用范围：{_render_paths(item.applies_to_paths)}",
        (
            f"- 信号：`{_escape_inline(item.strength)}` / "
            f"`{_escape_inline(item.origin)}` / "
            f"`{_escape_inline(item.recall_mode)}`"
        ),
        (
            f"- Revision：`{item.revision}`；ID：`{_escape_inline(item.id)}`；"
            f"Logical ID：`{_escape_inline(item.logical_id)}`"
        ),
        f"- 更新时间：{_escape_inline(item.updated_at)}",
    ]
    if item.aliases:
        lines.append(f"- Aliases：{_render_terms(item.aliases)}")
    if item.keywords:
        lines.append(f"- Keywords：{_render_terms(item.keywords)}")
    lines.extend(("", "**当前 revision 依据**", ""))
    if not item.sources:
        lines.append("- 暂无来源摘录。")
    else:
        for source in item.sources[:MAX_READ_SOURCES]:
            lines.extend(
                (
                    (
                        f"- `{_escape_inline(source.role)}` · "
                        f"{_escape_inline(source.created_at)} · "
                        f"turn `{_escape_inline(source.turn_id)}`"
                    ),
                    "",
                    _quote_block(
                        compact_text(source.quote, MAX_SOURCE_EXCERPT_CHARS)
                    ),
                )
            )
    lines.append("")
    return lines


def _quote_block(value: str) -> str:
    escaped = html.escape(str(value).replace("\r\n", "\n"), quote=False)
    return "\n".join(">" if not line else f"> {line}" for line in escaped.split("\n"))


def _escape_inline(value: object) -> str:
    compact = " ".join(str(value).replace("\r\n", "\n").split())
    return html.escape(compact, quote=False)


def _render_paths(paths: Sequence[str]) -> str:
    if not paths:
        return "项目全局"
    return "、".join(f"`{_escape_inline(path)}`" for path in paths)


def _render_terms(values: Sequence[str]) -> str:
    return "、".join(_escape_inline(value) for value in values)
