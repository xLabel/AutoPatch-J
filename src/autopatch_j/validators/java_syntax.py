from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SyntaxValidationResult:
    """Java 语法校验结果模型"""
    status: str
    message: str
    errors: list[str] = field(default_factory=list)


class JavaSyntaxValidator:
    """
    Java 语法验证器 (Validator)
    职责：使用 Tree-sitter 检查 Java 代码片段的合法性。
    """

    def validate(self, file_path: str, source_code: str) -> SyntaxValidationResult:
        """验证给定文件的 Java 语法"""
        if not file_path.lower().endswith(".java"):
            return SyntaxValidationResult(status="skipped", message="非 Java 文件，跳过语法校验。")

        try:
            from tree_sitter import Language, Parser
            import tree_sitter_java as tsjava
        except ImportError:
            return SyntaxValidationResult(
                status="unavailable",
                message="系统缺少 tree-sitter 或 tree-sitter-java 依赖，无法执行语法校验。"
            )

        try:
            language = Language(tsjava.language())
            parser = Parser(language)
            tree = parser.parse(source_code.encode("utf-8"))
            
            errors = self._collect_errors(tree.root_node)
            if errors:
                return SyntaxValidationResult(
                    status="error",
                    message=f"检测到 {len(errors)} 处 Java 语法错误。",
                    errors=errors
                )
            
            return SyntaxValidationResult(status="ok", message="Java 语法校验通过。")
        except Exception as e:
            return SyntaxValidationResult(status="error", message=f"语法分析过程出现异常：{str(e)}")

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
