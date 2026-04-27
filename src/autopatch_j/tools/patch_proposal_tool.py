from __future__ import annotations

import re
from typing import Any

from autopatch_j.core.finding_snippet_service import FindingSnippetService
from autopatch_j.tools.base import Tool, ToolResult


class PatchProposalTool(Tool):
    """
    补丁提案工具 (Adapter Layer)
    职责：基于 search-replace 逻辑生成补丁草案，不直接修改磁盘文件。
    """

    name = "propose_patch"
    description = (
        "提交一个针对特定漏洞的修复补丁提案（草案）。"
        "执行该工具不会修改文件系统，草案会进入待确认队列。"
        "在调用前应先通过 read_source_code 确认目标代码内容。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件相对路径。"},
            "old_string": {"type": "string", "description": "要被替换的原始代码精确块。"},
            "new_string": {"type": "string", "description": "替换后的新代码块。"},
            "rationale": {"type": "string", "description": "说明修复依据。"},
            "associated_finding_id": {
                "type": "string",
                "description": "关联的 finding 句柄，如 F1，用于语义校验与 workflow 推进。",
            },
        },
        "required": ["file_path", "old_string", "new_string", "rationale"],
    }

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str,
        associated_finding_id: str | None = None,
    ) -> ToolResult:
        assert self.context is not None
        patch_engine = self.context.patch_engine
        artifact_manager = self.context.artifact_manager
        patch_verifier = self.context.patch_verifier

        if not self.context.is_path_in_focus(file_path):
            allowed = ", ".join(self.context.focus_paths)
            return ToolResult(
                status="error",
                message=f"焦点约束阻止越界修复：{file_path} 不在当前允许范围内。允许路径：{allowed}",
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
            finding = self._fetch_associated_finding(artifact_manager=artifact_manager, finding_id=associated_finding_id)
            if finding is not None:
                target_rule = finding.check_id
                target_snippet = FindingSnippetService(self.context.repo_root).fetch_resolved_snippet(
                    file_path=finding.path,
                    start_line=finding.start_line,
                    end_line=finding.end_line,
                    fallback_snippet=finding.snippet,
                )

        from autopatch_j.core.patch_engine import (
            PatchDraft,
            TargetFileNotFoundError,
            OldStringNotFoundError,
            OldStringNotUniqueError,
        )

        try:
            new_code, patch_diff = patch_engine.create_draft(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            )
        except TargetFileNotFoundError as e:
            return ToolResult(
                status="error",
                message=f"补丁提案生成失败：{str(e)}",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": associated_finding_id,
                    "error_code": "FILE_NOT_FOUND",
                    "error_message": str(e),
                    "resolved_snippet": target_snippet,
                },
            )
        except OldStringNotFoundError as e:
            return ToolResult(
                status="error",
                message=f"补丁提案生成失败：{str(e)}",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": associated_finding_id,
                    "error_code": "OLD_STRING_NOT_FOUND",
                    "error_message": str(e),
                    "resolved_snippet": target_snippet,
                },
            )
        except OldStringNotUniqueError as e:
            return ToolResult(
                status="error",
                message=f"补丁提案生成失败：old_string 匹配了 {e.occurrences} 处，匹配不唯一。",
                payload={
                    "file_path": file_path,
                    "associated_finding_id": associated_finding_id,
                    "error_code": "OLD_STRING_NOT_UNIQUE",
                    "error_message": f"old_string 匹配了 {e.occurrences} 处，匹配不唯一。",
                    "resolved_snippet": target_snippet,
                },
            )

        validation_result = patch_verifier.verify_syntax(file_path, new_code)

        if validation_result.status == "unavailable":
            status = "unavailable"
        elif validation_result.status in ("ok", "skipped"):
            status = "ok"
        else:
            status = "invalid"

        message_status = "补丁起草成功并已通过语法校验。" if status == "ok" else validation_result.message

        draft = PatchDraft(
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

        artifact_manager.save_pending_patch(draft)
        message = f"补丁草案已加入队列。目标文件：{file_path}。\n"
        message += f"语法校验：{draft.validation.status}。\n"
        message += f"差异预览：\n{draft.diff}\n\n"
        if draft.status == "invalid":
            message += f"警告：补丁导致语法错误（{draft.validation.message}），请及时修正方案。"
        else:
            message += "此补丁正在等待人工确认。"
        return ToolResult(
            status=draft.status,
            message=message,
            payload={
                "file_path": file_path,
                "associated_finding_id": associated_finding_id,
                "validation": draft.validation.status,
            },
        )

    def _fetch_associated_finding(self, artifact_manager: Any, finding_id: str) -> Any:
        match = re.match(r"[Ff](\d+)", finding_id)
        if match is None:
            return None
        finding_index = int(match.group(1)) - 1
        scan_files = sorted(artifact_manager.findings_dir.glob("scan-*.json"), reverse=True)
        if not scan_files:
            return None
        return artifact_manager.get_finding_by_index(scan_files[0].stem, finding_index)
