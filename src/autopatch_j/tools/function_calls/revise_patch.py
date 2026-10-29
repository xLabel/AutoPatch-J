"""模型修订当前草案的业务入口，保持原文件及 finding 绑定。"""

from __future__ import annotations

from typing import Annotated

import pydantic

from autopatch_j.tools import contract
from autopatch_j.tools import names
from autopatch_j.tools import search_replace_draft_builder
from autopatch_j.tools.function_calls import patch_result


class RevisePatchTool(contract.BusinessTool):
    """生成当前待确认补丁的替代草稿。

    工具不改文件、不影响后续补丁队列；workflow 会在 ReAct 结束后替换当前补丁。
    """

    name = names.FunctionToolName.REVISE_PATCH.value

    def execute(
        self,
        file_path: Annotated[
            str,
            pydantic.Field(
                description="仓库内目标 Java 文件的相对路径，必须来自 finding 详情或源码读取工具结果。",
                strict=True,
            ),
        ],
        old_string: Annotated[
            str,
            pydantic.Field(
                description="要替换的原始代码精确片段；调用前必须用 get_finding_det"
                "ail、read_source_context、read_source"
                "_block 或 read_source_file 确认，必须和当前源"
                "码完全一致，不要省略缩进或上下文。",
                strict=True,
            ),
        ],
        new_string: Annotated[
            str,
            pydantic.Field(
                description="替换后的完整代码片段，只包含 old_string 对应区域的新内容。",
                strict=True,
            ),
        ],
        rationale: Annotated[
            str,
            pydantic.Field(
                description="简要说明为什么这样修复，以及修复依据来自哪个 finding 或源码证据。",
                strict=True,
            ),
        ],
        associated_finding_id: Annotated[
            str | None,
            pydantic.Field(
                description="当前扫描快照内的 finding 句柄，如 F1。它不是扫描规则 ID"
                "；修订扫描 finding 补丁时必须保持当前关联。",
                strict=True,
            ),
        ] = None,
    ) -> contract.ToolExecutionResult:
        """生成当前待确认补丁的替代草稿。

        Returns:
            保持当前审核绑定的修订结果；仅暂存，原草案仍待人工审核。
        """
        context = self.context
        current_draft = context.workspace_manager.load_current_patch_draft()
        if current_draft is None:
            return contract.ToolExecutionResult(
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
        current_finding_id = current_draft.associated_finding_id
        if current_draft.error_code == "STALE_DRAFT":
            return contract.ToolExecutionResult(
                status="error",
                message=current_draft.message,
                summary=f"补丁修订失败 (binding 已失效): {file_path}",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": current_finding_id,
                    "error_code": "STALE_DRAFT",
                    "error_message": current_draft.message,
                },
            )
        draft_result = search_replace_draft_builder.SearchReplaceDraftBuilder(
            context
        ).build_revision(
            current_draft=current_draft,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            rationale=rationale,
            associated_finding_id=associated_finding_id,
            action=search_replace_draft_builder.PatchDraftAction(
                action_label="补丁修订", focus_verb="修订"
            ),
        )
        if isinstance(draft_result, contract.ToolExecutionResult):
            return draft_result
        context.set_revised_patch_draft(draft_result)
        return patch_result.build_patch_success_result(
            draft=draft_result,
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            summary=f"已修订当前补丁: {file_path}",
            final_message="此修订草稿将在本轮结束后替换当前待确认补丁。",
        )
