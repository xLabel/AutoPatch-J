from __future__ import annotations

from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.tools.base import Tool, ToolResult
from autopatch_j.tools.patch_draft_builder import build_patch_draft


class PatchRevisionTool(Tool):
    """
    当前待确认补丁的修订工具。

    只为正在 review 的补丁生成替代 PatchDraft，并暂存在本轮 AgentSession；
    ReAct 结束后由 workflow 替换队头补丁，不影响后续补丁队列。
    """

    name = "revise_patch"
    description = (
        "重写当前待确认补丁。执行该工具不会修改文件系统，也不会影响后续补丁队列。"
        "修订草案会由 workflow 在 ReAct 结束后替换当前补丁。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件相对路径。"},
            "old_string": {"type": "string", "description": "要被替换的原始代码精确块。"},
            "new_string": {"type": "string", "description": "替换后的新代码块。"},
            "rationale": {"type": "string", "description": "说明修订依据。"},
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
            action_label="补丁修订",
            focus_verb="修订",
        )
        if isinstance(draft_result, ToolResult):
            return draft_result

        draft: PatchDraft = draft_result
        self.context.set_revised_patch_draft(draft)
        message = f"当前补丁修订草案已生成。目标文件：{file_path}。\n"
        message += f"语法校验：{draft.validation.status}。\n"
        message += f"差异预览：\n{draft.diff}\n\n"
        if draft.status == "invalid":
            message += f"警告：修订草案导致语法错误（{draft.validation.message}），请及时修正方案。"
        else:
            message += "此修订草案将在本轮结束后替换当前待确认补丁。"
        return ToolResult(
            status=draft.status,
            message=message,
            summary=f"已修订当前补丁: {file_path}",
            payload={
                "file_path": file_path,
                "associated_finding_id": associated_finding_id,
                "validation": draft.validation.status,
            },
        )
