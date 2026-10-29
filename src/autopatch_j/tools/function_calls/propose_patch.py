"""模型提出新草案的业务入口，不直接修改源码或队列。"""

from __future__ import annotations

from typing import Annotated

import pydantic

from autopatch_j.tools import contract
from autopatch_j.tools import names
from autopatch_j.tools import search_replace_draft_builder
from autopatch_j.tools.function_calls import patch_result


class ProposePatchTool(contract.BusinessTool):
    """生成新的待确认补丁草稿。

    工具不改文件、不入队；workflow 会在 ReAct 成功结束后统一确认入队。
    """

    name = names.FunctionToolName.PROPOSE_PATCH.value

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
                "；处理扫描 finding 时必须传入。",
                strict=True,
            ),
        ] = None,
    ) -> contract.ToolExecutionResult:
        """生成新的待确认补丁草稿。

        Returns:
            草案的匹配和语法检查结果；仅暂存，不写入源码或审核队列。
        """
        context = self.context
        draft_result = search_replace_draft_builder.SearchReplaceDraftBuilder(
            context
        ).build(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            rationale=rationale,
            associated_finding_id=associated_finding_id,
            action=search_replace_draft_builder.PatchDraftAction(
                action_label="补丁提案", focus_verb="修复"
            ),
        )
        if isinstance(draft_result, contract.ToolExecutionResult):
            context.clear_proposed_patch_draft()
            return draft_result
        context.set_proposed_patch_draft(draft_result)
        return patch_result.build_patch_success_result(
            draft=draft_result,
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            summary=f"已起草补丁草稿: {file_path}",
            final_message="此补丁将在本轮任务成功后进入人工确认队列。",
        )
