from __future__ import annotations

from typing import cast, TYPE_CHECKING

from autopatch_j.scanners import DEFAULT_SCANNER_NAME, JavaScanner, get_scanner
from autopatch_j.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from autopatch_j.core.service_context import ServiceContext


class ProjectScannerTool(Tool):
    """
    项目扫描工具 (Explorer)
    职责：执行静态分析并发现漏洞，返回逻辑句柄(F1, F2)供后续追溯。
    """
    name = "scan_project"
    description = (
        "执行 Java 项目静态扫描以发现安全漏洞。注意：此工具仅返回问题的摘要及逻辑句柄（如 F1, F2），"
        "旨在保持上下文精简。如果你需要看具体漏洞的代码，请在获得句柄后调用 get_finding_detail。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要扫描的文件或目录路径列表。若要扫描整个项目，请明确传入 ['.']。"
            }
        },
        "required": ["scope"]
    }

    def execute(self, scope: list[str] | None = None) -> ToolResult:
        assert self.context is not None
        scanner = cast(JavaScanner | None, get_scanner(DEFAULT_SCANNER_NAME))
        if not scanner:
            return ToolResult(status="error", message=f"未找到默认扫描器：{DEFAULT_SCANNER_NAME}")

        result = scanner.scan(self.context.repo_root, scope or ["."])
        artifact_id = self.context.artifacts.save_scan_result(result)

        findings_count = len(result.findings)
        summary = f"扫描完成 [ID: {artifact_id}]，共发现 {findings_count} 个问题。\n\n"
        
        if findings_count > 0:
            summary += "漏洞摘要表（请根据 F 编号调用 get_finding_detail 获取详情）：\n"
            for i, f in enumerate(result.findings, 1):
                summary += f"- F{i}: [{f.severity}] {f.path}:{f.start_line} ({f.check_id})\n"
        else:
            summary += "✔ 恭喜，未发现任何安全或正确性问题。"

        return ToolResult(
            status="ok",
            message=summary,
            payload={"artifact_id": artifact_id, "count": findings_count}
        )
