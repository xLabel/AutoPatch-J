from __future__ import annotations

from pathlib import Path
from typing import Any

from autopatch_j.core.patching.types import (
    ProjectValidationResult,
    SearchReplacePatchDraft,
    SyntaxCheckResult,
    VerificationResult,
)
from autopatch_j.scanners.contracts import StaticScanner


class PatchQualityVerifier:
    """
    补丁质量验证服务。

    职责边界：
    1. 草案阶段用 Tree-sitter 做 Java 语法检查。
    2. apply 后通过扫描器重扫确认目标规则是否消失。
    3. 不生成补丁、不写文件；补丁生成和落盘分别由 SearchReplacePatchEngine 负责。
    """

    def __init__(self, repo_root: Path, scanner: StaticScanner | None) -> None:
        self.repo_root = repo_root
        self.scanner = scanner

    def verify_syntax(self, file_path: str, new_source_code: str) -> SyntaxCheckResult:
        if not file_path.lower().endswith(".java"):
            return SyntaxCheckResult(status="skipped", message="非 Java 文件，跳过语法校验。")

        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava
        except ImportError:
            return SyntaxCheckResult(
                status="unavailable",
                message="系统缺少 tree-sitter 或 tree-sitter-java 依赖，无法执行语法校验。",
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
                    errors=errors,
                )

            return SyntaxCheckResult(status="ok", message="Java 语法校验通过。")
        except Exception as exc:
            return SyntaxCheckResult(status="error", message=f"语法分析过程出现异常：{str(exc)}")

    def describe_project_validation(self) -> ProjectValidationResult:
        build_files = [
            ("pom.xml", "Maven"),
            ("build.gradle", "Gradle"),
            ("build.gradle.kts", "Gradle"),
            ("settings.gradle", "Gradle"),
            ("settings.gradle.kts", "Gradle"),
        ]
        detected = [label for filename, label in build_files if (self.repo_root / filename).exists()]
        if not detected:
            return ProjectValidationResult(
                status="not_applicable",
                message="未检测到 Maven/Gradle 构建入口，未执行项目级验证。",
            )

        build_tool = detected[0]
        return ProjectValidationResult(
            status="not_run",
            message=f"检测到 {build_tool} 项目，默认未执行项目级编译验证。",
        )

    def _collect_errors(self, node: Any) -> list[str]:
        errors = []

        def walk(current_node):
            if current_node.is_error or current_node.is_missing:
                line = current_node.start_point[0] + 1
                col = current_node.start_point[1] + 1
                kind = "缺失符号" if current_node.is_missing else "语法错误"
                errors.append(f"[{line}:{col}] {kind} ({current_node.type})")

            for child in current_node.children:
                walk(child)

        walk(node)
        return errors

    def verify_finding_resolved(self, draft: SearchReplacePatchDraft) -> VerificationResult:
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
                return VerificationResult(
                    False,
                    f"语义校验失败：规则 '{check_id}' 在重扫中依然被触发，补丁逻辑可能不正确。",
                    len(rescan_result.findings),
                )

            return VerificationResult(True, f"精准校验通过：安全漏洞 '{check_id}' 已被成功消灭。", len(rescan_result.findings))

        if not rescan_result.findings:
            return VerificationResult(True, "全局校验通过：文件重扫未发现任何已知安全风险 (预防性修复生效)。")
        return VerificationResult(False, f"语义校验未通过：应用补丁后该文件依然存在 {len(rescan_result.findings)} 个漏洞发现。", len(rescan_result.findings))
