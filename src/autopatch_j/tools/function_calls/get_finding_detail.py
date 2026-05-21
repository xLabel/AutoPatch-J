from __future__ import annotations

import re
from typing import Annotated

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
            "读取最新扫描快照中的单个 finding 详情。用于把 F1/F2 这类逻辑句柄还原为规则 ID、"
            "文件位置、问题描述和当前源码片段。不会触发新扫描，也不会生成补丁。"
        ),
    )
    def execute(self, finding_id: Annotated[str, ToolArg("摘要表中的 finding 句柄，如 F1 或 F2。")]) -> ToolExecutionResult:
        context = self.require_context()
        artifact_manager = context.artifact_manager

        match = re.match(r"[Ff](\d+)", finding_id)
        if not match:
            return ToolExecutionResult(
                status="error",
                message=f"无效的 finding 句柄格式：{finding_id}。请使用 F1、F2 这种格式。",
                summary=f"获取失败: {finding_id}",
            )

        finding_index = int(match.group(1)) - 1
        scan_files = sorted(artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return ToolExecutionResult(
                status="error",
                message="系统中未找到扫描记录，请先发起一次代码检查。",
                summary="获取失败: 未找到扫描记录",
            )

        active_scan_id = scan_files[0].stem
        finding = artifact_manager.get_finding_by_index(active_scan_id, finding_index)
        if not finding:
            return ToolExecutionResult(
                status="error",
                message=f"无法从快照 {active_scan_id} 中取回句柄为 {finding_id} 的详情。",
                summary=f"获取失败: 未找到 finding {finding_id}",
            )

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

        message = f"漏洞详情取回成功 ({finding_id})\n"
        message += f"- **规则 ID**: {finding.check_id}\n"
        message += f"- **文件位置**: {finding.path}:{finding.start_line}\n"
        message += f"- **漏洞描述**: {finding.message}\n"
        message += f"- **代码证据**:\n```java\n{finding.snippet}\n```"

        return ToolExecutionResult(
            status="ok",
            message=message,
            summary=f"已获取 finding 详情: {finding_id}",
            payload=finding.to_dict(),
        )
