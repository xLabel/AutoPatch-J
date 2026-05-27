from __future__ import annotations

from typing import Annotated

from autopatch_j.tools.contract import FunctionTool, ToolArg, ToolExecutionResult, function_tool
from autopatch_j.tools.function_calls.patch_result import build_patch_success_result
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

    @function_tool(
        name=FunctionToolName.PROPOSE_PATCH,
        description=(
            "生成一个新的 search-replace 补丁草稿。调用前必须先用 get_finding_detail 或源码读取工具确认目标源码和 old_string。"
            "如果 old_string 不匹配，必须重新读取源码后再修正参数，不要猜测代码片段。"
            "执行后不会修改文件系统，也不会直接写入补丁队列；草稿会在本轮任务成功后由 workflow 处理。"
        ),
    )
    def execute(
        self,
        file_path: Annotated[str, ToolArg("仓库内目标 Java 文件的相对路径，必须来自 finding 详情或源码读取工具结果。")],
        old_string: Annotated[
            str,
            ToolArg(
                "要替换的原始代码精确片段；调用前必须用 get_finding_detail、read_source_context、"
                "read_source_block 或 read_source_file 确认，必须和当前源码完全一致，不要省略缩进或上下文。"
            ),
        ],
        new_string: Annotated[str, ToolArg("替换后的完整代码片段，只包含 old_string 对应区域的新内容。")],
        rationale: Annotated[str, ToolArg("简要说明为什么这样修复，以及修复依据来自哪个 finding 或源码证据。")],
        associated_finding_id: Annotated[
            str | None,
            ToolArg("当前扫描快照内的 finding 句柄，如 F1。它不是扫描规则 ID；处理扫描 finding 时必须传入。"),
        ] = None,
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
