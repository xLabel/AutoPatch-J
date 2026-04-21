from __future__ import annotations

from typing import TYPE_CHECKING
from autopatch_j.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from autopatch_j.core.service_context import ServiceContext


class PatchProposalTool(Tool):
    """
    补丁提案工具 (Planner)
    职责：基于‘查找-替换’逻辑生成补丁草案。注意：此工具绝不直接修改磁盘文件。
    """
    name = "propose_patch"
    description = (
        "提交一个针对特定漏洞的修复补丁提案（草案）。注意：执行此工具并不会修改文件系统，"
        "补丁将进入‘待审核’状态，直到用户手动确认。你必须在调用前通过 read_source_code 确认源码内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件相对路径。"},
            "old_string": {"type": "string", "description": "要被替换的原始代码精确块。必须确保该字符串在文件中是唯一的，且包含完整的缩进和换行，以便精确匹配。"},
            "new_string": {"type": "string", "description": "替换后的新代码块。必须保持与原代码一致的缩进风格和 Java 规范。"},
            "rationale": {"type": "string", "description": "说明此修复的逻辑依据。"},
            "associated_finding_id": {"type": "string", "description": "关联的漏洞句柄（如 F1），以便系统进行三级语义验证。"}
        },
        "required": ["file_path", "old_string", "new_string", "rationale"]
    }

    def execute(
        self, 
        file_path: str, 
        old_string: str, 
        new_string: str, 
        rationale: str,
        associated_finding_id: str | None = None
    ) -> ToolResult:
        assert self.context is not None
        engine = self.context.patch_engine
        am = self.context.artifacts

        target_rule = None
        target_snippet = None

        if associated_finding_id:
            import re
            match = re.match(r'[Ff](\d+)', associated_finding_id)
            if match:
                idx = int(match.group(1)) - 1
                scan_files = sorted(am.findings_dir.glob("scan-*.json"), reverse=True)
                if scan_files:
                    finding = am.get_finding_by_index(scan_files[0].stem, idx)
                    if finding:
                        target_rule = finding.check_id
                        target_snippet = finding.snippet

        draft = engine.create_draft(
            file_path, 
            old_string, 
            new_string,
            target_check_id=target_rule,
            target_snippet=target_snippet
        )

        if draft.status == "error":
            return ToolResult(status="error", message=f"补丁提案生成失败：{draft.message}")

        # 持久化草案
        am.save_pending_patch(draft)

        msg = f"补丁提案已成功生成并挂起。目标文件：{file_path}。\n"
        msg += f"语法校验：{draft.validation.status}。\n"
        msg += f"差异预览：\n{draft.diff}\n\n"
        
        if draft.status == "invalid":
            msg += f"❌ 警告：补丁导致语法错误（{draft.validation.message}），请及时修正方案。"
        else:
            msg += "💡 提示：此补丁正在等待人类审核。你可以告知用户查看预览并决定 apply 或 discard。"

        return ToolResult(
            status=draft.status,
            message=msg,
            payload={"file_path": file_path, "validation": draft.validation.status}
        )
