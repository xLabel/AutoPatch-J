from __future__ import annotations

import re
from typing import Any

from autopatch_j.core.finding_snippet_service import FindingSnippetService
from autopatch_j.core.patch_engine import (
    OldStringNotFoundError,
    OldStringNotUniqueError,
    PatchDraft,
    TargetFileNotFoundError,
)
from autopatch_j.tools.base import ToolContext, ToolResult


def build_patch_draft(
    context: ToolContext,
    file_path: str,
    old_string: str,
    new_string: str,
    rationale: str,
    associated_finding_id: str | None,
    *,
    action_label: str,
    focus_verb: str,
) -> PatchDraft | ToolResult:
    if not context.is_path_in_focus(file_path):
        allowed = ", ".join(context.focus_paths)
        return ToolResult(
            status="error",
            message=f"焦点约束阻止越界{focus_verb}：{file_path} 不在当前允许范围内。允许路径：{allowed}",
            summary=f"{focus_verb}越界: {file_path}",
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
        finding = _fetch_associated_finding(
            artifact_manager=context.artifact_manager,
            finding_id=associated_finding_id,
        )
        if finding is not None:
            target_rule = finding.check_id
            target_snippet = FindingSnippetService(context.repo_root).fetch_resolved_snippet(
                file_path=finding.path,
                start_line=finding.start_line,
                end_line=finding.end_line,
                fallback_snippet=finding.snippet,
            )

    try:
        new_code, patch_diff = context.patch_engine.create_draft(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
        )
    except TargetFileNotFoundError as exc:
        return _build_patch_error_result(
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            error_code="FILE_NOT_FOUND",
            error_message=str(exc),
            resolved_snippet=target_snippet,
            message=f"{action_label}生成失败：{str(exc)}",
            summary=f"补丁生成失败 (找不到文件): {file_path}",
        )
    except OldStringNotFoundError as exc:
        return _build_patch_error_result(
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            error_code="OLD_STRING_NOT_FOUND",
            error_message=str(exc),
            resolved_snippet=target_snippet,
            message=f"{action_label}生成失败：{str(exc)}",
            summary=f"补丁生成失败 (old_string 失配): {file_path}",
        )
    except OldStringNotUniqueError as exc:
        error_message = f"old_string 匹配了 {exc.occurrences} 处，匹配不唯一。"
        return _build_patch_error_result(
            file_path=file_path,
            associated_finding_id=associated_finding_id,
            error_code="OLD_STRING_NOT_UNIQUE",
            error_message=error_message,
            resolved_snippet=target_snippet,
            message=f"{action_label}生成失败：{error_message}",
            summary=f"补丁生成失败 (old_string 不唯一): {file_path}",
        )

    validation_result = context.patch_verifier.verify_syntax(file_path, new_code)
    if validation_result.status == "unavailable":
        status = "unavailable"
    elif validation_result.status in ("ok", "skipped"):
        status = "ok"
    else:
        status = "invalid"

    message_status = "补丁起草成功并已通过语法校验。" if status == "ok" else validation_result.message
    return PatchDraft(
        file_path=file_path,
        old_string=old_string,
        new_string=new_string,
        diff=patch_diff,
        validation=validation_result,
        status=status,
        message=message_status,
        rationale=rationale,
        source_hint=context.patch_source_hint,
        error_code=None,
        target_check_id=target_rule,
        target_snippet=target_snippet,
    )


def _build_patch_error_result(
    *,
    file_path: str,
    associated_finding_id: str | None,
    error_code: str,
    error_message: str,
    resolved_snippet: str | None,
    message: str,
    summary: str,
) -> ToolResult:
    return ToolResult(
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


def _fetch_associated_finding(artifact_manager: Any, finding_id: str) -> Any:
    match = re.match(r"[Ff](\d+)", finding_id)
    if match is None:
        return None
    finding_index = int(match.group(1)) - 1
    scan_files = sorted(artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
    if not scan_files:
        return None
    return artifact_manager.get_finding_by_index(scan_files[0].stem, finding_index)
