from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from autopatch_j.core.patching.types import (
    OldStringNotFoundError,
    OldStringNotUniqueError,
    SearchReplacePatchDraft,
    TargetFileNotFoundError,
)
from autopatch_j.core.project.repo_path import UnsafeRepoPathError, resolve_repo_path


class SearchReplacePatchEngine:
    """
    精确字符串替换补丁引擎。

    职责边界：
    1. 基于 old_string/new_string 生成内存草案和 unified diff。
    2. 在用户确认 apply 后按原文件换行风格安全写回磁盘。
    3. 不判断修复是否正确，也不做语法/语义校验；这些由 PatchQualityVerifier 负责。
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

    def apply_patch(self, draft: SearchReplacePatchDraft) -> bool:
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
        try:
            return resolve_repo_path(self.repo_root, file_path)
        except UnsafeRepoPathError as exc:
            raise PermissionError(f"安全风险拦截：路径 '{file_path}' 超出了项目根目录范围。") from exc

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
