from __future__ import annotations

from typing import Annotated

from autopatch_j.core.review.finding_lookup import FindingLookupError, resolve_finding_handle
from autopatch_j.tools.contract import (
    FunctionTool,
    ToolArg,
    ToolExecutionResult,
    function_tool,
)
from autopatch_j.tools.names import FunctionToolName


class GetFindingDetailTool(FunctionTool):
    """
    读取最新扫描快照中的 finding 详情。

    只把 CLI 摘要里的 F1/F2 还原为扫描器证据，不触发新扫描，也不生成补丁。
    """

    @function_tool(
        name=FunctionToolName.GET_FINDING_DETAIL,
        description=(
            "读取当前工作台扫描快照中的单个 finding 详情。用于把当前 scan 内的 F1/F2 这类逻辑句柄"
            "还原为规则 ID、文件位置、问题描述和当前源码片段。不会触发新扫描，也不会生成补丁。"
        ),
    )
    def execute(self, finding_id: Annotated[str, ToolArg("摘要表中的 finding 句柄，如 F1 或 F2。")]) -> ToolExecutionResult:
        context = self.require_context()
        try:
            lookup = resolve_finding_handle(context.artifact_manager, context.workspace_manager, finding_id)
        except FindingLookupError as exc:
            return ToolExecutionResult(
                status="error",
                message=str(exc),
                summary=f"获取失败: {finding_id}",
                payload={"finding_id": finding_id, "error_code": exc.code, "error_message": str(exc)},
            )
        finding = lookup.finding

        if not context.is_path_in_focus(finding.path):
            allowed = ", ".join(context.focus_paths)
            return ToolExecutionResult(
                status="error",
                message=f"焦点约束阻止越界取证：{finding.path} 不在当前允许范围内。允许路径：{allowed}",
                summary=f"取证越界: {finding.path}",
            )

        finding.snippet = context.code_fetcher.fetch_resolved_snippet(
            file_path=finding.path,
            start_line=finding.start_line,
            end_line=finding.end_line,
            fallback_snippet=finding.snippet,
        )

        message = f"漏洞详情取回成功 ({lookup.finding_id}, scan: {lookup.scan_id})\n"
        message += f"- **规则 ID**: {finding.check_id}\n"
        message += f"- **文件位置**: {finding.path}:{finding.start_line}\n"
        message += f"- **漏洞描述**: {finding.message}\n"
        message += f"- **代码证据**:\n```java\n{finding.snippet}\n```"
        payload = finding.to_dict()
        payload["scan_id"] = lookup.scan_id
        payload["finding_id"] = lookup.finding_id

        return ToolExecutionResult(
            status="ok",
            message=message,
            summary=f"已获取 finding 详情: {lookup.finding_id}",
            payload=payload,
        )
