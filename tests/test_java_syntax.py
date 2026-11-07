"""直接运行 Java 语法分析器，区分语法错误与不可验证。"""

import pytest

from autopatch_j.core import errors
from autopatch_j.core.project import java_ast
from autopatch_j.core.repair import checks


def test_java_syntax_real_parser_accepts_and_rejects():
    assert checks.check_java_syntax("A.java", "class A {}").status == "passed"
    assert checks.check_java_syntax("A.java", "class A {").status == "failed"
    assert (
        checks.check_java_syntax("notes.txt", "plain text").status == "skipped"
    )


def test_parser_runtime_failure_propagates(monkeypatch):
    def failed(*unused_args):
        raise RuntimeError("parser failure")

    monkeypatch.setattr(java_ast.tree_sitter, "Parser", failed)
    with pytest.raises(errors.OperationError, match="parser failure"):
        checks.check_java_syntax("A.java", "class A {}")


def test_syntax_check_does_not_require_java_compilation():
    assert (
        checks.check_java_syntax(
            "A.java", 'class A { Unknown value = "type mismatch"; }'
        ).status
        == "passed"
    )


def test_native_missing_node_has_location_without_rejecting_all_structure():
    source = java_ast.parse("class A { int n }")
    assert source.source_has_errors
    assert any(
        "missing" in issue and ":" in issue for issue in source.syntax_issues()
    )
