"""直接运行 Java 语法分析器，区分语法错误与不可验证。"""

from autopatch_j.core.repair import checks


def test_java_syntax_real_parser_accepts_and_rejects():
    assert checks.check_java("A.java", "class A {}").status == "passed"
    assert checks.check_java("A.java", "class A {").status == "failed"
    assert checks.check_java("notes.txt", "plain text").status == "skipped"


def test_dependency_unavailable_roundtrips_without_becoming_passed(monkeypatch):
    monkeypatch.setattr(
        checks, "tree_sitter_import_error", ImportError("missing tree-sitter")
    )
    result = checks.check_java("A.java", "class A {}")
    assert result.status == "unverified"
    assert "tree-sitter" in result.message
