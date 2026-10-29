"""源码工具共用的路径准入、行号验证和请求缓存。"""

from __future__ import annotations

import dataclasses
import pathlib

from autopatch_j.core.project import repo_path
from autopatch_j.tools import contract


@dataclasses.dataclass(frozen=True, slots=True)
class SourceReadTarget:
    context: contract.ToolRuntimeContext
    path: str


class SourceReadToolBase(contract.BusinessTool):
    """统一源码路径准入、行号验证及请求内缓存。"""

    def _prepare_target(
        self, path: str
    ) -> SourceReadTarget | contract.ToolExecutionResult:
        """复核真实源码准入；缺失路径仅接受唯一焦点内同名候选。"""
        context = self.context
        try:
            full_path = repo_path.resolve_repo_path(context.repo_root, path)
            normalized_path = repo_path.to_repo_relative_path(
                context.repo_root, full_path
            )
        except repo_path.UnsafeRepoPathError as exc:
            return contract.ToolExecutionResult(
                status="error",
                message=f"读取失败：{exc}",
                summary=f"读取失败: {path}",
            )
        if repo_path.is_project_state_path(normalized_path):
            return contract.ToolExecutionResult(
                status="error",
                message=f"读取失败：项目状态目录不属于源码范围：{normalized_path}",
                summary=f"读取失败: {normalized_path}",
            )
        if not full_path.exists():
            filename = pathlib.Path(normalized_path).name
            results = context.symbol_indexer.search(filename, limit=20)
            candidate_paths = []
            for entry in results:
                if pathlib.Path(
                    entry.path
                ).name == filename and context.is_path_in_focus(entry.path):
                    candidate_paths.append(entry.path)
            candidate_paths = sorted(set(candidate_paths))
            if len(candidate_paths) == 1:
                normalized_path = candidate_paths[0]
            elif len(candidate_paths) > 1:
                candidates = "\n".join(
                    (f"- {path}" for path in candidate_paths[:10])
                )
                return contract.ToolExecutionResult(
                    status="error",
                    message=(
                        "读取失败：路径不存在且同名候选不唯一："
                        f"{normalized_path}\n请改用明确路径：\n{candidates}"
                    ),
                    summary=f"读取失败: 同名候选不唯一 {filename}",
                    payload={
                        "path": normalized_path,
                        "candidates": [path for path in candidate_paths],
                    },
                )
            else:
                return contract.ToolExecutionResult(
                    status="error",
                    message=f"读取失败：找不到文件：{normalized_path}",
                    summary=f"读取失败: {normalized_path}",
                    payload={"path": normalized_path, "candidates": []},
                )
        if not context.is_path_in_focus(normalized_path):
            allowed = ", ".join(context.focus_paths)
            return contract.ToolExecutionResult(
                status="error",
                message=(
                    f"焦点约束阻止越界读取：{normalized_path}\n"
                    f"不在当前允许范围内。允许路径：{allowed}"
                ),
                summary=f"读取越界: {normalized_path}",
            )
        return SourceReadTarget(context=context, path=normalized_path)

    def _normalize_positive_line(
        self, line: int
    ) -> int | contract.ToolExecutionResult:
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            return contract.ToolExecutionResult(
                status="error",
                message=f"读取失败：line 必须是 1-based 正整数，当前值为 {line}",
                summary="读取失败: 无效行号",
            )
        normalized_line = line
        return normalized_line

    def _fetch_cached(
        self, context: contract.ToolRuntimeContext, path: str, line: int | None
    ) -> contract.ToolExecutionResult | None:
        return context.fetch_cached_source_read(
            tool_name=self.name, path=path, line=line
        )

    def _persist_cached(
        self,
        context: contract.ToolRuntimeContext,
        path: str,
        line: int | None,
        result: contract.ToolExecutionResult,
    ) -> None:
        context.persist_cached_source_read(
            tool_name=self.name, path=path, line=line, result=result
        )

    def _error_if_source_failed(
        self, code: str, path: str
    ) -> contract.ToolExecutionResult | None:
        if code.startswith("错误"):
            return contract.ToolExecutionResult(
                status="error", message=code, summary=f"读取失败: {path}"
            )
        return None
