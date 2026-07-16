from __future__ import annotations

from typing import Annotated

from autopatch_j.tools.contract import FunctionTool, ToolArg, ToolExecutionResult, function_tool
from autopatch_j.tools.function_calls.patch_result import build_patch_success_result
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

    @function_tool(
        name=FunctionToolName.REVISE_PATCH,
        description=(
            "按用户反馈重写当前待确认补丁，生成一个替代 search-replace 草稿。"
            "调用前必须先用 get_finding_detail 或源码读取工具确认目标源码和 old_string。"
            "如果用户只是询问补丁含义、原因、影响或风险，不要调用本工具。"
            "如果 old_string 不匹配，必须重新读取源码后再修正参数，不要猜测代码片段。"
            "执行后不会修改文件系统，也不会影响后续补丁队列；草稿会由 workflow 替换当前补丁。"
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
            ToolArg("当前扫描快照内的 finding 句柄，如 F1。它不是扫描规则 ID；修订扫描 finding 补丁时必须保持当前关联。"),
        ] = None,
    ) -> ToolExecutionResult:
        context = self.require_context()
        current_draft = context.workspace_manager.load_current_patch_draft()
        if current_draft is None:
            return ToolExecutionResult(
                status="error",
                message="补丁修订失败：当前没有待确认补丁。",
                summary="补丁修订失败 (无待确认补丁)",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": associated_finding_id,
                    "error_code": "NO_PENDING_PATCH",
                    "error_message": "当前没有待确认补丁。",
                },
            )
        if current_draft.error_code == "STALE_DRAFT":
            return ToolExecutionResult(
                status="error",
                message=current_draft.message,
                summary=f"补丁修订失败 (binding 已失效): {file_path}",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": current_draft.associated_finding_id,
                    "error_code": "STALE_DRAFT",
                    "error_message": current_draft.message,
                },
            )
        draft_result = SearchReplaceDraftBuilder(context).build_revision(
            current_draft=current_draft,
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
