from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autopatch_j.core.patch_engine import PatchDraft
from autopatch_j.scanners.base import JavaScanner


@dataclass(slots=True)
class SyntaxCheckResult:
    """Java 语法校验结果模型"""
    status: str
    message: str
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VerificationResult:
    """重扫复核结果模型"""
    is_resolved: bool
    message: str
    remaining_findings: int = 0


class PatchVerifier:
    """
    补丁质量验证中心 (QA Department)
    职责：内聚所有补丁级别的检验能力（AST 语法校验、Scanner 漏洞重扫校验等）。
    """

    def __init__(self, repo_root: Path, scanner: JavaScanner | None) -> None:
        self.repo_root = repo_root
        self.scanner = scanner

    def verify_syntax(self, file_path: str, new_source_code: str) -> SyntaxCheckResult:
        """
        [事前校验]：纯内存操作。
        使用 Tree-sitter 检查替换后的代码是否出现语法树断裂。
        """
        if not file_path.lower().endswith(".java"):
            return SyntaxCheckResult(status="skipped", message="非 Java 文件，跳过语法校验。")

        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava
        except ImportError:
            return SyntaxCheckResult(
                status="unavailable",
                message="系统缺少 tree-sitter 或 tree-sitter-java 依赖，无法执行语法校验。"
            )

        try:
            language = Language(tsjava.language())
            parser = Parser(language)
            tree = parser.parse(new_source_code.encode("utf-8"))
            
            errors = self._collect_errors(tree.root_node)
            if errors:
                return SyntaxCheckResult(
                    status="error",
                    message=f"检测到 {len(errors)} 处 Java 语法错误。",
                    errors=errors
                )
            
            return SyntaxCheckResult(status="ok", message="Java 语法校验通过。")
        except Exception as e:
            return SyntaxCheckResult(status="error", message=f"语法分析过程出现异常：{str(e)}")

    def _collect_errors(self, node: Any) -> list[str]:
        """递归遍历语法树，搜集 ERROR 或 MISSING 节点"""
        errors = []
        
        def walk(n):
            if n.is_error or n.is_missing:
                line = n.start_point[0] + 1
                col = n.start_point[1] + 1
                kind = "缺失符号" if n.is_missing else "语法错误"
                errors.append(f"[{line}:{col}] {kind} ({n.type})")
            
            for child in n.children:
                walk(child)
        
        walk(node)
        return errors

    def verify_finding_resolved(self, draft: PatchDraft) -> VerificationResult:
        """
        [事后复核]：调用 Scanner 执行增量重扫，验证补丁是否真正消灭了目标规则 (check_id)。
        """
        if not self.scanner:
            return VerificationResult(False, "语义重扫执行失败：未配置可用的扫描器。")

        rescan_result = self.scanner.scan(self.repo_root, [draft.file_path])

        if rescan_result.status != "ok":
            return VerificationResult(False, f"语义重扫执行失败：{rescan_result.message}")

        check_id = draft.target_check_id
        is_valid_check_id = bool(check_id and str(check_id).strip() and str(check_id) != "None")

        if is_valid_check_id:
            is_fixed = True
            for finding in rescan_result.findings:
                if finding.check_id == check_id:
                    if draft.target_snippet and draft.target_snippet in finding.snippet:
                        is_fixed = False
                        break

            if not is_fixed:
                return VerificationResult(False, f"语义校验失败：规则 '{check_id}' 在重扫中依然被触发，补丁逻辑可能不正确。", len(rescan_result.findings))

            return VerificationResult(True, f"精准校验通过：安全漏洞 '{check_id}' 已被成功消灭。", len(rescan_result.findings))

        if not rescan_result.findings:
            return VerificationResult(True, "全局校验通过：文件重扫未发现任何已知安全风险 (预防性修复生效)。")
        else:
            return VerificationResult(False, f"语义校验未通过：应用补丁后该文件依然存在 {len(rescan_result.findings)} 个漏洞发现。", len(rescan_result.findings))