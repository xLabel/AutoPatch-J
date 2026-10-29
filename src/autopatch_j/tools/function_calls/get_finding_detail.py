"""读取活动扫描快照中的 finding，并投影当前重定位证据。"""

from __future__ import annotations

import dataclasses
from typing import Annotated

import pydantic

from autopatch_j.core.review import finding_lookup
from autopatch_j.tools import contract
from autopatch_j.tools import names


class GetFindingDetailTool(contract.BusinessTool):
    """读取当前工作台中的 finding 详情。

    只把 CLI 摘要里的 F1/F2 还原为扫描器证据；当前 pending finding 使用
    workspace 中已重定位的 region。不触发新扫描，也不生成补丁。
    """

    name = names.FunctionToolName.GET_FINDING_DETAIL.value

    def execute(
        self,
        finding_id: Annotated[
            str,
            pydantic.Field(
                description="摘要表中的 finding 句柄，如 F1 或 F2。",
                strict=True,
            ),
        ],
    ) -> contract.ToolExecutionResult:
        """读取当前工作台中的 finding 详情。

        Returns:
            当前扫描快照中的 finding 详情或查找失败原因。
        """
        context = self.context
        try:
            lookup = finding_lookup.resolve_finding_handle(
                context.artifact_manager, context.workspace_manager, finding_id
            )
        except finding_lookup.FindingLookupError as exc:
            return contract.ToolExecutionResult(
                status="error",
                message=str(exc),
                summary=f"获取失败: {finding_id}",
                payload={
                    "finding_id": finding_id,
                    "error_code": exc.code,
                    "error_message": str(exc),
                },
            )
        finding = dataclasses.replace(lookup.finding)
        current_draft = context.workspace_manager.load_current_patch_draft()
        if (
            current_draft is not None
            and current_draft.error_code is None
            and (current_draft.associated_finding_id == lookup.finding_id)
            and (current_draft.source_scan_id == lookup.scan_id)
            and (current_draft.target_finding is not None)
        ):
            target = current_draft.target_finding
            finding = dataclasses.replace(
                finding,
                fingerprint=target.fingerprint,
                check_id=target.check_id,
                path=target.path,
                region=target.region,
            )
        if not context.is_path_in_focus(finding.path):
            allowed = ", ".join(context.focus_paths)
            return contract.ToolExecutionResult(
                status="error",
                message=f"焦点约束阻止越界取证：{finding.path} 不在当前允许范围内。允许路径：{allowed}",
                summary=f"取证越界: {finding.path}",
            )
        finding.snippet = context.code_fetcher.fetch_resolved_snippet(
            file_path=finding.path,
            start_line=finding.region.start_line,
            end_line=finding.region.inclusive_end_line,
            fallback_snippet=finding.snippet,
        )
        message = (
            f"漏洞详情取回成功 ({lookup.finding_id}, scan: {lookup.scan_id})\n"
        )
        message += f"- **规则 ID**: {finding.check_id}\n"
        message += f"- **Fingerprint**: {finding.fingerprint}\n"
        message += (
            f"- **文件位置**: {finding.path}"
            f":{finding.region.start_line}"
            f":{finding.region.start_column}"
            f"\n"
        )
        message += f"- **漏洞描述**: {finding.message}\n"
        message += f"- **代码证据**:\n```java\n{finding.snippet}\n```"
        payload = finding.to_dict()
        payload["scan_id"] = lookup.scan_id
        payload["finding_id"] = lookup.finding_id
        return contract.ToolExecutionResult(
            status="ok",
            message=message,
            summary=f"已获取 finding 详情: {lookup.finding_id}",
            payload=payload,
        )
