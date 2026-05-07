from __future__ import annotations

from autopatch_j.core.patching import SearchReplacePatchDraft
from autopatch_j.tools.contract import ToolExecutionResult


def build_patch_success_result(
    *,
    draft: SearchReplacePatchDraft,
    file_path: str,
    associated_finding_id: str | None,
    summary: str,
    final_message: str,
) -> ToolExecutionResult:
    message = f"补丁草稿已生成，等待流程确认。目标文件：{file_path}。\n"
    message += f"语法校验：{draft.validation.status}。\n"
    message += f"差异预览：\n{draft.diff}\n\n"
    if draft.status == "invalid":
        message += f"警告：补丁导致语法错误（{draft.validation.message}），请及时修正方案。"
    else:
        message += final_message
    return ToolExecutionResult(
        status=draft.status,
        message=message,
        summary=summary,
        payload={
            "file_path": file_path,
            "associated_finding_id": associated_finding_id,
            "validation": draft.validation.status,
        },
    )
