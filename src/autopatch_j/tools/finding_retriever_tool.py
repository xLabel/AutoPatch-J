from __future__ import annotations

import re

from autopatch_j.core.finding_snippet_service import FindingSnippetService
from autopatch_j.tools.base import Tool, ToolResult


class FindingRetrieverTool(Tool):
    """
    漏洞证据检索工具。
    职责：将逻辑句柄 (F1, F2) 映射回真实的漏洞详情。
    """

    name = "get_finding_detail"
    description = (
        "取回漏洞的详细证据。这是进行任何修复前的必要步骤。"
        "输入逻辑句柄（如 F1），返回该漏洞的规则 ID、完整消息以及触发问题的代码片段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "finding_id": {"type": "string", "description": "摘要表中的逻辑句柄，如 'F1'。"}
        },
        "required": ["finding_id"],
    }

    def execute(self, finding_id: str) -> ToolResult:
        assert self.context is not None
        artifacts = self.context.artifacts

        match = re.match(r"[Ff](\d+)", finding_id)
        if not match:
            return ToolResult(
                status="error",
                message=f"无效的漏洞句柄格式：{finding_id}。请使用 F1、F2 这种格式。",
            )

        finding_index = int(match.group(1)) - 1
        scan_files = sorted(artifacts.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return ToolResult(status="error", message="系统中未找到扫描记录，请先发起一次代码检查。")

        active_scan_id = scan_files[0].stem
        finding = artifacts.fetch_finding_by_index(active_scan_id, finding_index)
        if not finding:
            return ToolResult(
                status="error",
                message=f"无法从快照 {active_scan_id} 中取回句柄为 {finding_id} 的详情。",
            )

        if not self.context.is_path_in_focus(finding.path):
            allowed = ", ".join(self.context.focus_paths)
            return ToolResult(
                status="error",
                message=f"焦点约束阻止越界取证：{finding.path} 不在当前允许范围内。允许路径：{allowed}",
            )

        finding.snippet = FindingSnippetService(self.context.repo_root).fetch_resolved_snippet(
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
        return ToolResult(status="ok", message=message, payload=finding.to_dict())
