from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autopatch_j.core.project import UnsafeRepoPathError, resolve_repo_path, to_repo_relative_path
from autopatch_j.tools.contract import FunctionTool, ToolExecutionResult, ToolRuntimeContext


@dataclass(frozen=True, slots=True)
class SourceReadTarget:
    context: ToolRuntimeContext
    path: str


class SourceReadToolBase(FunctionTool):
    def _prepare_target(self, path: str) -> SourceReadTarget | ToolExecutionResult:
        context = self.require_context()
        try:
            full_path = resolve_repo_path(context.repo_root, path)
            normalized_path = to_repo_relative_path(context.repo_root, full_path)
        except UnsafeRepoPathError as exc:
            return ToolExecutionResult(status="error", message=f"读取失败：{exc}", summary=f"读取失败: {path}")

        if not full_path.exists():
            filename = Path(normalized_path).name
            results = context.symbol_indexer.search(filename, limit=1)
            if results:
                normalized_path = results[0].path

        if not context.is_path_in_focus(normalized_path):
            allowed = ", ".join(context.focus_paths)
            return ToolExecutionResult(
                status="error",
                message=f"焦点约束阻止越界读取：{normalized_path}\n不在当前允许范围内。允许路径：{allowed}",
                summary=f"读取越界: {normalized_path}",
            )

        return SourceReadTarget(context=context, path=normalized_path)

    def _normalize_positive_line(self, line: int) -> int | ToolExecutionResult:
        try:
            normalized_line = int(line)
        except (TypeError, ValueError):
            return ToolExecutionResult(
                status="error",
                message=f"读取失败：line 必须是 1-based 正整数，当前值为 {line}",
                summary=f"读取失败: 无效行号 {line}",
            )
        if normalized_line < 1:
            return ToolExecutionResult(
                status="error",
                message=f"读取失败：line 必须是 1-based 正整数，当前值为 {normalized_line}",
                summary=f"读取失败: 无效行号 {normalized_line}",
            )
        return normalized_line

    def _fetch_cached(self, context: ToolRuntimeContext, path: str, line: int | None) -> ToolExecutionResult | None:
        return context.fetch_cached_source_read(tool_name=self.name, path=path, line=line)

    def _persist_cached(
        self,
        context: ToolRuntimeContext,
        path: str,
        line: int | None,
        result: ToolExecutionResult,
    ) -> None:
        context.persist_cached_source_read(tool_name=self.name, path=path, line=line, result=result)

    def _error_if_source_failed(self, code: str, path: str) -> ToolExecutionResult | None:
        if code.startswith("错误"):
            return ToolExecutionResult(status="error", message=code, summary=f"读取失败: {path}")
        return None
