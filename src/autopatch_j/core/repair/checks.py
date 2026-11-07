"""候选语法验收；输入语法错误与分析器运行故障分开处理。"""

from autopatch_j.core.project import java_ast
from autopatch_j.core.repair import models


def check_java_syntax(path: str, content: str) -> models.Check:
    """使用原生语法诊断验收候选；不编译或执行业务测试。"""
    if not path.lower().endswith(".java"):
        return models.Check(
            kind="java_syntax",
            status="skipped",
            message="非 Java 文件，未检查语法。",
        )
    source = java_ast.parse(content.lstrip("\ufeff"))
    if source.source_has_errors:
        return models.Check(
            kind="java_syntax",
            status="failed",
            message="Java 语法错误：" + "；".join(source.syntax_issues()),
        )
    return models.Check(
        kind="java_syntax",
        status="passed",
        message="Java 语法通过；未执行编译或测试。",
    )
