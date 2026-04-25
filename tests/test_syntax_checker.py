from __future__ import annotations

import sys
import types

import pytest

from autopatch_j.validators.java_syntax import JavaSyntaxValidator


def test_java_syntax_validator_returns_ok_when_tree_sitter_accepts_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeNode:
        is_error = False
        is_missing = False
        start_point = (0, 0)
        children = []

    class FakeLanguage:
        def __init__(self, _language) -> None:
            pass

    class FakeParser:
        def __init__(self, _language: FakeLanguage) -> None:
            pass

        def parse(self, _content: bytes):
            return types.SimpleNamespace(root_node=FakeNode())

    monkeypatch.setitem(sys.modules, "tree_sitter", types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser))
    monkeypatch.setitem(sys.modules, "tree_sitter_java", types.SimpleNamespace(language=lambda: object()))

    validator = JavaSyntaxValidator()
    result = validator.validate("Demo.java", "class Demo {}")

    assert result.status == "ok"


def test_java_syntax_validator_returns_error_when_tree_has_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeNode:
        is_error = True
        is_missing = False
        start_point = (1, 2)
        children = []
        type = "ERROR"

    class FakeLanguage:
        def __init__(self, _language) -> None:
            pass

    class FakeParser:
        def __init__(self, _language: FakeLanguage) -> None:
            pass

        def parse(self, _content: bytes):
            return types.SimpleNamespace(root_node=FakeNode())

    monkeypatch.setitem(sys.modules, "tree_sitter", types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser))
    monkeypatch.setitem(sys.modules, "tree_sitter_java", types.SimpleNamespace(language=lambda: object()))

    validator = JavaSyntaxValidator()
    result = validator.validate("Demo.java", "class Demo {")

    assert result.status == "error"
    assert result.errors


def test_java_syntax_validator_returns_unavailable_when_dependency_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"tree_sitter", "tree_sitter_java"}:
            raise ImportError(f"missing dependency: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    validator = JavaSyntaxValidator()
    result = validator.validate("Demo.java", "class Demo {}")

    assert result.status == "unavailable"
    assert "tree-sitter" in result.message
