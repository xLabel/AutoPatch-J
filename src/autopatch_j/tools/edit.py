from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from autopatch_j.tools.base import Tool, ToolExecutionResult, ToolName
from autopatch_j.validators import (
    DEFAULT_VALIDATOR_NAME,
    SyntaxValidationResult,
    SyntaxValidator,
    TreeSitterJavaValidator,
    get_validator,
)


@dataclass(slots=True)
class SearchReplaceEdit:
    file_path: str
    old_string: str
    new_string: str


@dataclass(slots=True)
class EditPreview:
    file_path: str
    status: str
    message: str
    occurrences: int
    diff: str
    validation: SyntaxValidationResult


class PreviewSearchReplaceTool(Tool):
    name = ToolName.PREVIEW_SEARCH_REPLACE
    description = "Preview a single search-replace edit and validate Java syntax when possible."
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    def execute(
        self,
        repo_root: Path,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> ToolExecutionResult:
        preview = preview_search_replace(
            repo_root,
            SearchReplaceEdit(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            ),
        )
        return ToolExecutionResult(
            tool_name=self.name,
            status=preview.status,
            message=preview.message,
            payload=preview,
        )


class ApplySearchReplaceTool(Tool):
    name = ToolName.APPLY_SEARCH_REPLACE
    description = "Apply a single search-replace edit after preview validation passes."
    parameters = PreviewSearchReplaceTool.parameters

    def execute(
        self,
        repo_root: Path,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> ToolExecutionResult:
        preview = apply_search_replace(
            repo_root,
            SearchReplaceEdit(
                file_path=file_path,
                old_string=old_string,
                new_string=new_string,
            ),
        )
        return ToolExecutionResult(
            tool_name=self.name,
            status=preview.status,
            message=preview.message,
            payload=preview,
        )


def preview_search_replace(
    repo_root: Path,
    edit: SearchReplaceEdit,
    context_lines: int = 3,
    validator: SyntaxValidator | None = None,
) -> EditPreview:
    target = resolve_repo_file(repo_root, edit.file_path)
    if not target.exists() or not target.is_file():
        return EditPreview(
            file_path=edit.file_path,
            status="error",
            message="目标文件不存在。",
            occurrences=0,
            diff="",
            validation=SyntaxValidationResult(
                status="skipped",
                message="目标文件缺失，已跳过语法校验。",
            ),
        )

    original = read_text(target)
    occurrences = original.count(edit.old_string)
    if occurrences == 0:
        return EditPreview(
            file_path=edit.file_path,
            status="missing",
            message="old_string was not found in the target file.",
            occurrences=0,
            diff="",
            validation=SyntaxValidationResult(
                status="skipped",
                message="edit 无法预览，已跳过语法校验。",
            ),
        )
    if occurrences > 1:
        return EditPreview(
            file_path=edit.file_path,
            status="ambiguous",
            message="old_string matched multiple locations in the target file.",
            occurrences=occurrences,
            diff="",
            validation=SyntaxValidationResult(
                status="skipped",
                message="edit 匹配位置不唯一，已跳过语法校验。",
            ),
        )

    updated = original.replace(edit.old_string, edit.new_string, 1)
    diff = build_unified_diff(edit.file_path, original, updated, context_lines=context_lines)
    active_validator = validator or get_validator(DEFAULT_VALIDATOR_NAME) or TreeSitterJavaValidator()
    validation = active_validator.validate(edit.file_path, updated)
    return EditPreview(
        file_path=edit.file_path,
        status="ok",
        message="edit 预览生成成功。",
        occurrences=1,
        diff=diff,
        validation=validation,
    )


def apply_search_replace(
    repo_root: Path,
    edit: SearchReplaceEdit,
    context_lines: int = 3,
    validator: SyntaxValidator | None = None,
) -> EditPreview:
    preview = preview_search_replace(
        repo_root,
        edit,
        context_lines=context_lines,
        validator=validator,
    )
    if preview.status != "ok":
        return preview
    if preview.validation.status != "ok" and Path(edit.file_path).suffix.lower() == ".java":
        return EditPreview(
            file_path=edit.file_path,
            status="blocked",
            message=(
                "Java edit 语法校验通过前禁止应用。"
                f"当前校验状态：{preview.validation.status}。"
            ),
            occurrences=preview.occurrences,
            diff=preview.diff,
            validation=preview.validation,
        )

    target = resolve_repo_file(repo_root, edit.file_path)
    original = read_text(target)
    updated = original.replace(edit.old_string, edit.new_string, 1)
    target.write_text(updated, encoding="utf-8")
    return preview


def build_unified_diff(
    file_path: str,
    original: str,
    updated: str,
    context_lines: int = 3,
) -> str:
    original_lines = original.splitlines(keepends=True)
    updated_lines = updated.splitlines(keepends=True)
    diff_lines = unified_diff(
        original_lines,
        updated_lines,
        fromfile=f"a/{file_path}",
        tofile=f"b/{file_path}",
        n=context_lines,
    )
    return "".join(diff_lines)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def resolve_repo_file(repo_root: Path, file_path: str) -> Path:
    candidate = (repo_root / file_path).resolve()
    candidate.relative_to(repo_root.resolve())
    return candidate
