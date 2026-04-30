from __future__ import annotations

from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.patch_draft_builder import build_patch_draft


class PatchProposalTool(Tool):
    """
    新补丁起草工具。

    用 search-replace 输入生成单个 PatchDraft，并暂存在本轮 AgentSession；
    ReAct 成功结束后由 workflow 统一确认入队，工具本身不写磁盘、不直接改队列。
    """

    name = "propose_patch"
    description = (
        "提交一个针对特定漏洞的修复补丁提案（草案）。"
        "执行该工具不会修改文件系统，草案会进入待确认队列。"
        "在调用前应先通过 read_source_code 确认目标代码内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件相对路径。"},
            "old_string": {"type": "string", "description": "要被替换的原始代码精确块。"},
            "new_string": {"type": "string", "description": "替换后的新代码块。"},
            "rationale": {"type": "string", "description": "说明修复依据。"},
            "associated_finding_id": {
                "type": "string",
                "description": "关联的 finding 句柄，如 F1，用于语义校验与 workflow 推进。",
            },
        },
        "required": ["file_path", "old_string", "new_string", "rationale"],
    }

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        associated_finding_id: str | None = None,
    ) -> ToolResult:
        assert self.context is not None
        draft_result = build_patch_draft(
            context=self.context,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            rationale=rationale,
            associated_finding_id=associated_finding_id,
            action_label="补丁提案",
            focus_verb="修复",
        )
        if isinstance(draft_result, ToolResult):
            self.context.clear_proposed_patch_draft()
            return draft_result

        draft: PatchDraft = draft_result
        self.context.set_proposed_patch_draft(draft)
        message = f"补丁草案已生成，等待流程确认入队。目标文件：{file_path}。\n"
        message += f"语法校验：{draft.validation.status}。\n"
        message += f"差异预览：\n{draft.diff}\n\n"
        if draft.status == "invalid":
            message += f"警告：补丁导致语法错误（{draft.validation.message}），请及时修正方案。"
        else:
            message += "此补丁将在本轮任务成功后进入人工确认队列。"
        return ToolResult(
            status=draft.status,
            message=message,
            summary=f"已起草补丁草案: {file_path}",
            payload={
                "file_path": file_path,
                "associated_finding_id": associated_finding_id,
                "validation": draft.validation.status,
            },
        )
