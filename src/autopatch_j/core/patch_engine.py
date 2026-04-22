from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

from autopatch_j.validators.java_syntax import SyntaxValidationResult, JavaSyntaxValidator


@dataclass(slots=True)
class PatchDraft:
    """补丁草案的数据模型"""
    file_path: str
    old_string: str
    new_string: str
    diff: str
    validation: SyntaxValidationResult
    status: str  # "ok", "error", "invalid"
    message: str
    rationale: str | None = None
    # 语义验证元数据
    target_check_id: str | None = None
    target_snippet: str | None = None


class PatchEngine:
    """
    核心补丁引擎 (Core Service)
    职责：实现 Search-Replace 补丁的起草、语法验证和物理落盘。
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
        target_snippet: str | None = None
    ) -> PatchDraft:
        """
        创建一个补丁草案并进行验证。
        """
        target_path = self._resolve_safe_path(file_path)
        
        if not target_path.exists() or not target_path.is_file():
            return self._build_error_draft(file_path, "目标文件不存在。")

        content = self._read_file_content(target_path)
        
        # 🚀 深度加固：全流程 LF 归一化处理
        # 无论是 Windows(CRLF) 还是 Unix(LF)，在内存中统一作为 LF 处理逻辑
        norm_content = content.replace("\r\n", "\n")
        norm_old = old_string.replace("\r\n", "\n")
        norm_new = new_string.replace("\r\n", "\n")

        occurrences = norm_content.count(norm_old)
        if occurrences == 0:
            return self._build_error_draft(file_path, "在文件中未找到指定的 old_string（内容不匹配）。")
        if occurrences > 1:
            return self._build_error_draft(file_path, f"old_string 匹配了 {occurrences} 处，匹配不唯一。")

        # 在归一化后的内容上执行替换
        updated_norm_content = norm_content.replace(norm_old, norm_new, 1)
        
        # 生成 Diff (基于归一化内容，确保 Diff 展示的一致性)
        patch_diff = self._generate_unified_diff(file_path, norm_content, updated_norm_content)

        # 语法门禁 (基于归一化后的内容验证)
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
            target_check_id=target_check_id,
            target_snippet=target_snippet
        )

    def perform_apply(self, draft: PatchDraft) -> bool:
        """
        将经过确认的补丁物理落盘，并保持原文件的换行符风格。
        """
        target_path = self._resolve_safe_path(draft.file_path)
        if not target_path.exists():
            return False

        content = self._read_file_content(target_path)

        # 探测风格：CRLF or LF
        newline = "\r\n" if "\r\n" in content else "\n"

        norm_content = content.replace("\r\n", "\n")
        norm_old = draft.old_string.replace("\r\n", "\n")
        norm_new = draft.new_string.replace("\r\n", "\n")

        if norm_content.count(norm_old) != 1:
            return False

        updated_norm = norm_content.replace(norm_old, norm_new, 1)

        # 🚀 物理级修复：使用 newline 参数进行原子写入，不再手动 replace("\n", "\r\n")
        # 这能彻底杜绝因 \r\r\n 导致的空行膨胀问题
        with open(target_path, "w", encoding="utf-8", newline=newline) as f:
            f.write(updated_norm)

        return True

    def _resolve_safe_path(self, file_path: str) -> Path:
        repo_abs = self.repo_root.resolve()
        target_abs = (repo_abs / file_path).resolve()
        try:
            target_abs.relative_to(repo_abs)
        except ValueError:
            raise PermissionError(f"安全风险拦截：路径 '{file_path}' 超出了项目根目录范围。")
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
            n=3
        )
        return "".join(diff)

    def _build_error_draft(self, file_path: str, message: str) -> PatchDraft:
        return PatchDraft(
            file_path=file_path,
            old_string="",
            new_string="",
            diff="",
            validation=SyntaxValidationResult(status="error", message=message),
            status="error",
            message=message,
            rationale=None,
            target_check_id=None,
            target_snippet=None
        )
