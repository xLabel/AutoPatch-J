from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autopatch_j.core.patch_verifier import SyntaxCheckResult


class TargetFileNotFoundError(Exception):
    """补丁目标文件不存在。"""

    pass


class OldStringNotFoundError(Exception):
    """old_string 没有在目标文件中精确命中。"""

    pass


class OldStringNotUniqueError(Exception):
    """old_string 在目标文件中命中多处，无法安全替换。"""

    def __init__(self, occurrences: int):
        self.occurrences = occurrences
        super().__init__(f"old_string matched {occurrences} times.")


@dataclass(slots=True)
class PatchDraft:
    """
    内存中的补丁草案。

    保存 search-replace 输入、diff、验证结果和关联 finding 信息；进入 workspace 前会转换为 PatchDraftData。
    """

    file_path: str
    old_string: str
    new_string: str
    diff: str
    validation: SyntaxCheckResult
    status: str  # "ok", "error", "invalid", "unavailable"
    message: str
    rationale: str | None = None
    source_hint: str | None = None
    error_code: str | None = None
    target_check_id: str | None = None
    target_snippet: str | None = None


class PatchEngine:
    """
    精确字符串替换补丁引擎。

    职责边界：
    1. 基于 old_string/new_string 生成内存草案和 unified diff。
    2. 在用户确认 apply 后按原文件换行风格安全写回磁盘。
    3. 不判断修复是否正确，也不做语法/语义校验；这些由 PatchVerifier 负责。
    """

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def create_draft(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
    ) -> tuple[str, str]:
        target_path = self._resolve_safe_path(file_path)
        if not target_path.exists() or not target_path.is_file():
            raise TargetFileNotFoundError("目标文件不存在。")

        content = self._read_file_content(target_path)
        norm_content = content.replace("\r\n", "\n")
        norm_old = old_string.replace("\r\n", "\n")
        norm_new = new_string.replace("\r\n", "\n")

        occurrences = norm_content.count(norm_old)
        if occurrences == 0:
            raise OldStringNotFoundError("在文件中未找到指定的 old_string（内容不匹配）。")
        if occurrences > 1:
            raise OldStringNotUniqueError(occurrences)

        updated_norm_content = norm_content.replace(norm_old, norm_new, 1)
        patch_diff = self._generate_unified_diff(file_path, norm_content, updated_norm_content)
        return updated_norm_content, patch_diff

    def apply_patch(self, draft: PatchDraft) -> bool:
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
