from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from autopatch_j.validators.java_syntax import JavaSyntaxValidator, SyntaxValidationResult


@dataclass(slots=True)
class PatchDraft:
    """补丁草案的数据模型。"""

    file_path: str
    old_string: str
    new_string: str
    diff: str
    validation: SyntaxValidationResult
    status: str  # "ok", "error", "invalid", "unavailable"
    message: str
    rationale: str | None = None
    error_code: str | None = None
    target_check_id: str | None = None
    target_snippet: str | None = None


class PatchEngine:
    """
    核心补丁引擎 (Core Service)
    职责：负责 search-replace 草案生成、语法校验和物理落盘。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self.validator = JavaSyntaxValidator()

    def perform_draft(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        rationale: str | None = None,
        target_check_id: str | None = None,
        target_snippet: str | None = None,
    ) -> PatchDraft:
        target_path = self._resolve_safe_path(file_path)
        if not target_path.exists() or not target_path.is_file():
            return self._build_error_draft(
                file_path=file_path,
                message="目标文件不存在。",
                error_code="FILE_NOT_FOUND",
            )

        content = self._read_file_content(target_path)
        norm_content = content.replace("\r\n", "\n")
        norm_old = old_string.replace("\r\n", "\n")
        norm_new = new_string.replace("\r\n", "\n")

        occurrences = norm_content.count(norm_old)
        if occurrences == 0:
            return self._build_error_draft(
                file_path=file_path,
                message="在文件中未找到指定的 old_string（内容不匹配）。",
                error_code="OLD_STRING_NOT_FOUND",
            )
        if occurrences > 1:
            return self._build_error_draft(
                file_path=file_path,
                message=f"old_string 匹配了 {occurrences} 处，匹配不唯一。",
                error_code="OLD_STRING_NOT_UNIQUE",
            )

        updated_norm_content = norm_content.replace(norm_old, norm_new, 1)
        patch_diff = self._generate_unified_diff(file_path, norm_content, updated_norm_content)
        validation_result = self.validator.validate(file_path, updated_norm_content)

        if validation_result.status == "unavailable":
            status = "unavailable"
        elif validation_result.status in ("ok", "skipped"):
            status = "ok"
        else:
            status = "invalid"

        message = "补丁起草成功并已通过语法校验。" if status == "ok" else validation_result.message
        return PatchDraft(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            diff=patch_diff,
            validation=validation_result,
            status=status,
            message=message,
            rationale=rationale,
            error_code=None,
            target_check_id=target_check_id,
            target_snippet=target_snippet,
        )

    def perform_apply(self, draft: PatchDraft) -> bool:
        target_path = self._resolve_safe_path(draft.file_path)
        if not target_path.exists():
            return False

        content = self._read_file_content(target_path)
        newline = "\r\n" if "\r\n" in content else "\n"

        norm_content = content.replace("\r\n", "\n")
        norm_old = draft.old_string.replace("\r\n", "\n")
        norm_new = draft.new_string.replace("\r\n", "\n")
        if norm_content.count(norm_old) != 1:
            return False

        updated_norm = norm_content.replace(norm_old, norm_new, 1)
        with open(target_path, "w", encoding="utf-8", newline=newline) as handle:
            handle.write(updated_norm)
        return True

    def _resolve_safe_path(self, file_path: str) -> Path:
        repo_abs = self.repo_root.resolve()
        target_abs = (repo_abs / file_path).resolve()
        try:
            target_abs.relative_to(repo_abs)
        except ValueError as exc:
            raise PermissionError(f"安全风险拦截：路径 '{file_path}' 超出了项目根目录范围。") from exc
        return target_abs

    def _read_file_content(self, path: Path) -> str:
        raw_bytes = path.read_bytes()
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return raw_bytes.decode("gbk")
            except UnicodeDecodeError:
                return raw_bytes.decode("utf-8", errors="replace")

    def _generate_unified_diff(self, file_path: str, old_text: str, new_text: str) -> str:
        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=3,
        )
        return "".join(diff)

    def _build_error_draft(self, file_path: str, message: str, error_code: str) -> PatchDraft:
        return PatchDraft(
            file_path=file_path,
            old_string="",
            new_string="",
            diff="",
            validation=SyntaxValidationResult(status="error", message=message),
            status="error",
            message=message,
            rationale=None,
            error_code=error_code,
            target_check_id=None,
            target_snippet=None,
        )
