from __future__ import annotations

from autopatch_j.tools.contract import FunctionTool, FunctionToolSpec, ToolExecutionResult
from autopatch_j.tools.function_calls.patch_result import build_patch_success_result
from autopatch_j.tools.function_calls.patch_schema import PATCH_DRAFT_PARAMETERS
from autopatch_j.tools.names import FunctionToolName
from autopatch_j.tools.search_replace_draft_builder import (
    PatchDraftAction,
    SearchReplaceDraftBuilder,
)


class ProposePatchTool(FunctionTool):
    """
    生成新的待确认补丁草稿。

    工具不改文件、不入队；workflow 会在 ReAct 成功结束后统一确认入队。
    """

    spec = FunctionToolSpec(
        name=FunctionToolName.PROPOSE_PATCH,
        description=(
            "生成一个新的 search-replace 补丁草稿。调用前必须先用 get_finding_detail 或源码读取工具确认目标源码和 old_string。"
            "如果 old_string 不匹配，必须重新读取源码后再修正参数，不要猜测代码片段。"
            "执行后不会修改文件系统，也不会直接写入补丁队列；草稿会在本轮任务成功后由 workflow 处理。"
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
            action=PatchDraftAction(action_label="补丁提案", focus_verb="修复"),
        )
        if isinstance(draft_result, ToolExecutionResult):
            context.clear_proposed_patch_draft()
            return draft_result

        context.set_proposed_patch_draft(draft_result)
        return build_patch_success_result(
            draft=draft_result,
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            summary=f"已起草补丁草稿: {file_path}",
            final_message="此补丁将在本轮任务成功后进入人工确认队列。",
        )
