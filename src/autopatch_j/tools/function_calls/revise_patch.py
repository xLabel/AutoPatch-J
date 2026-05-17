from __future__ import annotations

from autopatch_j.tools.contract import FunctionTool, FunctionToolSpec, ToolExecutionResult
from autopatch_j.tools.function_calls.patch_result import build_patch_success_result
from autopatch_j.tools.function_calls.patch_schema import PATCH_DRAFT_PARAMETERS
from autopatch_j.tools.names import FunctionToolName
from autopatch_j.tools.search_replace_draft_builder import (
    PatchDraftAction,
    SearchReplaceDraftBuilder,
)


class RevisePatchTool(FunctionTool):
    """
    生成当前待确认补丁的替代草稿。

    工具不改文件、不影响后续补丁队列；workflow 会在 ReAct 结束后替换当前补丁。
    """

    spec = FunctionToolSpec(
        name=FunctionToolName.REVISE_PATCH,
        description=(
            "按用户反馈重写当前待确认补丁，生成一个替代 search-replace 草稿。"
            "调用前必须先用 get_finding_detail 或源码读取工具确认目标源码和 old_string。"
            "如果用户只是询问补丁含义、原因、影响或风险，不要调用本工具。"
            "如果 old_string 不匹配，必须重新读取源码后再修正参数，不要猜测代码片段。"
            "执行后不会修改文件系统，也不会影响后续补丁队列；草稿会由 workflow 替换当前补丁。"
        ),
        parameters=PATCH_DRAFT_PARAMETERS,
    )

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        associated_finding_id: str | None = None,
    ) -> ToolExecutionResult:
        context = self.require_context()
        draft_result = SearchReplaceDraftBuilder(context).build(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            rationale=rationale,
            associated_finding_id=associated_finding_id,
            action=PatchDraftAction(action_label="补丁修订", focus_verb="修订"),
        )
        if isinstance(draft_result, ToolExecutionResult):
            return draft_result

        context.set_revised_patch_draft(draft_result)
        return build_patch_success_result(
            draft=draft_result,
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            summary=f"已修订当前补丁: {file_path}",
            final_message="此修订草稿将在本轮结束后替换当前待确认补丁。",
        )
