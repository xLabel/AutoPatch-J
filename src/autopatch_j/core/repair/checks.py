"""Java 语法证据，分析器不可用与已确认语法错误分别表达。"""

from autopatch_j.core.repair import models

tree_sitter_import_error: ImportError | None = None
try:
    import tree_sitter
    import tree_sitter_java
except ImportError as exc:
    tree_sitter_import_error = exc


def check_java(path: str, content: str) -> models.Check:
    """返回候选文本的语法证据；不编译或执行业务测试。"""
    if not path.lower().endswith(".java"):
        return models.Check(
            kind="java_syntax",
            status="skipped",
            message="非 Java 文件，未检查语法。",
        )
    if tree_sitter_import_error is not None:
        return models.Check(
            kind="java_syntax",
            status="unverified",
            message="tree-sitter 依赖不可用，未验证 Java 语法。",
        )
    try:
        parser = tree_sitter.Parser(
            tree_sitter.Language(tree_sitter_java.language())
        )
        tree = parser.parse(content.lstrip("\ufeff").encode("utf-8"))
        pending = [tree.root_node]
        errors = []
        while pending:
            node = pending.pop()
            if node.is_error or node.is_missing:
                errors.append(
                    f"{node.start_point.row + 1}:"
                    f"{node.start_point.column + 1} {node.type}"
                )
            pending.extend(reversed(node.children))
    except (OSError, RuntimeError, ValueError) as exc:
        return models.Check(
            kind="java_syntax",
            status="unverified",
            message=f"语法分析未完成：{exc}",
        )
    if errors:
        return models.Check(
            kind="java_syntax",
            status="failed",
            message="Java 语法错误：" + "；".join(errors),
        )
    return models.Check(
        kind="java_syntax",
        status="passed",
        message="Java 语法通过；未执行编译或测试。",
    )
