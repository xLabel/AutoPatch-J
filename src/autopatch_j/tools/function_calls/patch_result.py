"""草案工具的观察结果，明确暂存与人工入队边界。"""

from __future__ import annotations

from autopatch_j.core.patching import types
from autopatch_j.tools import contract


def build_patch_success_result(
    *,
    draft: types.SearchReplacePatchDraft,
    file_path: str,
    associated_finding_id: str | None,
    summary: str,
    final_message: str,
) -> contract.ToolExecutionResult:
    """将草案和真实语法状态转为观察，不代表工作台已入队或源码已写入。"""
    message = f"补丁草稿已生成，等待流程确认。目标文件：{file_path}。\n"
    message += f"语法校验：{draft.validation.status}。\n"
    message += f"差异预览：\n{draft.diff}\n\n"
    if draft.status == "invalid":
        message += f"警告：补丁导致语法错误（{draft.validation.message}），请及时修正方案。"
    else:
        message += final_message
    return contract.ToolExecutionResult(
        status=draft.status,
        message=message,
        summary=summary,
        payload={
            "file_path": file_path,
            "associated_finding_id": draft.associated_finding_id
            or associated_finding_id,
            "source_scan_id": draft.source_scan_id,
            "target_finding": (
                draft.target_finding.to_dict() if draft.target_finding else None
            ),
            "validation": draft.validation.status,
        },
    )
