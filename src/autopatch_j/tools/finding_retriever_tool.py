from __future__ import annotations

import re
from typing import TYPE_CHECKING
from autopatch_j.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from autopatch_j.agent.agent import AutoPatchAgent


class FindingRetrieverTool(Tool):
    """
    漏洞证据检索工具 (Retriever)
    职责：将逻辑句柄(F1, F2)映射回真实的漏洞详情。
    """
    name = "get_finding_detail"
    description = (
        "取回漏洞的详细证据。这是进行任何修复前的必须步骤。"
        "输入逻辑句柄（如 F1），返回该漏洞的规则 ID、完整消息以及引发问题的代码片段。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "finding_id": {"type": "string", "description": "摘要表中的逻辑句柄，如 'F1'。"}
        },
        "required": ["finding_id"]
    }

    def execute(self, finding_id: str) -> ToolResult:
        assert self.context is not None
        am = self.context.artifacts
        
        match = re.match(r'[Ff](\d+)', finding_id)
        if not match:
            return ToolResult(status="error", message=f"无效的漏洞句柄格式：{finding_id}。请使用 F1, F2 这种格式。")
        
        idx = int(match.group(1)) - 1

        scan_files = sorted(am.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return ToolResult(status="error", message="系统中未找到扫描记录，请先执行 scan_project。")
        
        active_scan_id = scan_files[0].stem
        finding = am.fetch_finding_by_index(active_scan_id, idx)
        
        if not finding:
            return ToolResult(status="error", message=f"无法从快照 {active_scan_id} 中取回句柄为 {finding_id} 的详情。")
        if not self.context.is_path_in_focus(finding.path):
            allowed = ", ".join(self.context.focus_paths)
            return ToolResult(status="error", message=f"焦点约束阻止越界取证：{finding.path} 不在当前允许范围内。允许路径：{allowed}")

        msg = f"漏洞详情取回成功 ({finding_id})\n"
        msg += f"- **规则 ID**: {finding.check_id}\n"
        msg += f"- **文件位置**: {finding.path}:{finding.start_line}\n"
        msg += f"- **漏洞描述**: {finding.message}\n"
        msg += f"- **代码证据**:\n```java\n{finding.snippet}\n```"

        return ToolResult(status="ok", message=msg, payload=finding.to_dict())
