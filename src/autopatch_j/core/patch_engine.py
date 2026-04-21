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

    def create_draft(
        self, 
        file_path: str, 
        old_string: str, 
        new_string: str,
        target_check_id: str | None = None,
        target_snippet: str | None = None
    ) -> PatchDraft:
        """
        创建一个补丁草案并进行验证。
        """
        target_path = self._resolve_safe_path(file_path)
        
        # 1. 物理门禁：检查文件
        if not target_path.exists() or not target_path.is_file():
            return self._build_error_draft(file_path, "目标文件不存在。")

        content = self._read_file_content(target_path)
        
        # 2. 物理门禁：检查唯一性
        occurrences = content.count(old_string)
        if occurrences == 0:
            return self._build_error_draft(file_path, "在文件中未找到指定的 old_string（查找失败）。")
        if occurrences > 1:
            return self._build_error_draft(file_path, f"old_string 匹配了 {occurrences} 处，匹配不唯一。")

        # 3. 模拟替换并生成 Diff
        updated_content = content.replace(old_string, new_string, 1)
        patch_diff = self._generate_unified_diff(file_path, content, updated_content)

        # 4. 语法门禁
        validation_result = self.validator.validate(file_path, updated_content)
        
        status = "ok" if validation_result.status in ("ok", "skipped") else "invalid"
        message = "补丁起草成功并已通过语法校验。" if status == "ok" else f"语法校验失败：{validation_result.message}"

        return PatchDraft(
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            diff=patch_diff,
            validation=validation_result,
            status=status,
            message=message,
            target_check_id=target_check_id,
            target_snippet=target_snippet
        )

    def apply_patch(self, draft: PatchDraft) -> bool:
        """
        将经过确认的补丁物理落盘。
        """
        target_path = self._resolve_safe_path(draft.file_path)
        if not target_path.exists():
            return False

        # 再次确认物理一致性，防止在 Pending 期间文件被外部修改
        current_content = self._read_file_content(target_path)
        if current_content.count(draft.old_string) != 1:
            return False

        new_content = current_content.replace(draft.old_string, draft.new_string, 1)
        target_path.write_text(new_content, encoding="utf-8")
        return True

    def _resolve_safe_path(self, file_path: str) -> Path:
        """
        解析并验证安全路径。
        确保目标路径严格位于 repo_root 之内，防止路径穿越 (Path Traversal)。
        """
        # 1. 组合并解析为绝对路径
        repo_abs = self.repo_root.resolve()
        target_abs = (repo_abs / file_path).resolve()
        
        # 2. 严格校验：目标必须在仓库目录树内
        try:
            target_abs.relative_to(repo_abs)
        except ValueError:
            # 如果不是相对关系，说明发生了路径穿越（如使用 ../../）
            raise PermissionError(f"安全风险拦截：路径 '{file_path}' 超出了项目根目录范围。")
            
        return target_abs

    def _read_file_content(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # 容错处理：对于非 UTF-8 编码尝试带替换的读取
            return path.read_text(encoding="utf-8", errors="replace")

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
            message=message
        )
