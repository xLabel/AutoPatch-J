from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from autopatch_j.core.patching import (
    OldStringNotFoundError,
    OldStringNotUniqueError,
    SearchReplacePatchDraft,
    SyntaxCheckResult,
    TargetFileNotFoundError,
)
from autopatch_j.tools.contract import ToolExecutionResult, ToolRuntimeContext


PATCH_DRAFT_PARAMETERS = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "仓库内目标 Java 文件的相对路径，必须来自 read_source_code 或 finding 详情。",
        },
        "old_string": {
            "type": "string",
            "description": "要替换的原始代码精确片段；必须和当前源码完全一致，不要省略缩进或上下文。",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的完整代码片段，只包含 old_string 对应区域的新内容。",
        },
        "rationale": {
            "type": "string",
            "description": "简要说明为什么这样修复，以及修复依据来自哪个 finding 或源码证据。",
        },
        "associated_finding_id": {
            "type": "string",
            "description": "关联的 finding 句柄，如 F1。处理扫描 finding 时必须传入；无 finding 的轻量复核可省略。",
        },
    },
    "required": ["file_path", "old_string", "new_string", "rationale"],
}


@dataclass(frozen=True, slots=True)
class PatchDraftAction:
    action_label: str
    focus_verb: str


class SearchReplaceDraftFactory:
    """
    search-replace 补丁草稿的共享生成器。

    propose_patch 和 revise_patch 的流程语义不同，但路径约束、finding 绑定、
    old_string 错误归一化和语法校验应保持一致。
    """

    def __init__(self, context: ToolRuntimeContext) -> None:
        self.context = context

    def build(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        associated_finding_id: str | None,
        action: PatchDraftAction,
    ) -> SearchReplacePatchDraft | ToolExecutionResult:
        if not self.context.is_path_in_focus(file_path):
            allowed = ", ".join(self.context.focus_paths)
            return ToolExecutionResult(
                status="error",
                message=(
                    f"焦点约束阻止越界{action.focus_verb}：{file_path} "
                    f"不在当前允许范围内。允许路径：{allowed}"
                ),
                summary=f"{action.focus_verb}越界: {file_path}",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": associated_finding_id,
                    "error_code": "OUT_OF_FOCUS",
                    "error_message": "目标文件超出焦点范围。",
                },
            )

        target_rule: str | None = None
        target_snippet: str | None = None
        if associated_finding_id:
            finding = self._fetch_associated_finding(associated_finding_id)
            if finding is not None:
                target_rule = finding.check_id
                target_snippet = self.context.code_fetcher.fetch_resolved_snippet(
                    file_path=finding.path,
                    start_line=finding.start_line,
                    end_line=finding.end_line,
                    fallback_snippet=finding.snippet,
                )

        try:
            new_code, patch_diff = self.context.patch_engine.create_draft(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            )
        except TargetFileNotFoundError as exc:
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=associated_finding_id,
                error_code="FILE_NOT_FOUND",
                error_message=str(exc),
                resolved_snippet=target_snippet,
                message=f"{action.action_label}生成失败：{exc}",
                summary=f"补丁生成失败 (找不到文件): {file_path}",
            )
        except OldStringNotFoundError as exc:
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=associated_finding_id,
                error_code="OLD_STRING_NOT_FOUND",
                error_message=str(exc),
                resolved_snippet=target_snippet,
                message=f"{action.action_label}生成失败：{exc}",
                summary=f"补丁生成失败 (old_string 失配): {file_path}",
            )
        except OldStringNotUniqueError as exc:
            error_message = f"old_string 匹配了 {exc.occurrences} 处，匹配不唯一。"
            return self._build_error_result(
                file_path=file_path,
                associated_finding_id=associated_finding_id,
                error_code="OLD_STRING_NOT_UNIQUE",
                error_message=error_message,
                resolved_snippet=target_snippet,
                message=f"{action.action_label}生成失败：{error_message}",
                summary=f"补丁生成失败 (old_string 不唯一): {file_path}",
            )

        validation_result = self._verify_syntax(file_path, new_code)
        if validation_result.status == "unavailable":
            status = "unavailable"
        elif validation_result.status in {"ok", "skipped"}:
            status = "ok"
        else:
            status = "invalid"

        message_status = "补丁起草成功并已通过语法校验。" if status == "ok" else validation_result.message
        return SearchReplacePatchDraft(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            diff=patch_diff,
            validation=validation_result,
            status=status,
            message=message_status,
            rationale=rationale,
            source_hint=self.context.patch_source_hint,
            error_code=None,
            target_check_id=target_rule,
            target_snippet=target_snippet,
        )

    def _verify_syntax(self, file_path: str, new_code: str) -> SyntaxCheckResult:
        verifier = self.context.patch_verifier
        if verifier is None:
            return SyntaxCheckResult(status="unavailable", message="未配置补丁语法校验器。")
        return verifier.verify_syntax(file_path, new_code)

    def _build_error_result(
        self,
        *,
        file_path: str,
        associated_finding_id: str | None,
        error_code: str,
        error_message: str,
        resolved_snippet: str | None,
        message: str,
        summary: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            status="error",
            message=message,
            summary=summary,
            payload={
                "file_path": file_path,
                "associated_finding_id": associated_finding_id,
                "error_code": error_code,
                "error_message": error_message,
                "resolved_snippet": resolved_snippet,
            },
        )

    def _fetch_associated_finding(self, finding_id: str) -> Any:
        match = re.match(r"[Ff](\d+)", finding_id)
        if match is None:
            return None
        finding_index = int(match.group(1)) - 1
        scan_files = sorted(self.context.artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return None
        return self.context.artifact_manager.get_finding_by_index(scan_files[0].stem, finding_index)


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
