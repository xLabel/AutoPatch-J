"""替代 Tree-sitter 对象验证语法结果语义，不声称执行 Java 编译。"""

from __future__ import annotations

import pathlib
import types

import pytest

from autopatch_j.core.patching import verification


def test_java_syntax_validator_returns_ok_when_tree_sitter_accepts_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNode:
        is_error = False
        is_missing = False
        start_point = (0, 0)
        children = []

    class FakeLanguage:
        def __init__(self, unused_language) -> None:
            pass

    class FakeParser:
        def __init__(self, unused_language: FakeLanguage) -> None:
            pass

        def parse(self, unused_content: bytes):
            return types.SimpleNamespace(root_node=FakeNode())

    monkeypatch.setattr(
        verification,
        "tree_sitter",
        types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser),
    )
    monkeypatch.setattr(
        verification,
        "tree_sitter_java",
        types.SimpleNamespace(language=object),
    )

    validator = verification.PatchQualityVerifier(pathlib.Path("."), None)
    result = validator.verify_syntax("Demo.java", "class Demo {}")

    assert result.status == "ok"


def test_java_syntax_validator_returns_error_when_tree_has_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeNode:
        is_error = True
        is_missing = False
        start_point = (1, 2)
        children = []
        type = "ERROR"

    class FakeLanguage:
        def __init__(self, unused_language) -> None:
            pass

    class FakeParser:
        def __init__(self, unused_language: FakeLanguage) -> None:
            pass

        def parse(self, unused_content: bytes):
            return types.SimpleNamespace(root_node=FakeNode())

    monkeypatch.setattr(
        verification,
        "tree_sitter",
        types.SimpleNamespace(Language=FakeLanguage, Parser=FakeParser),
    )
    monkeypatch.setattr(
        verification,
        "tree_sitter_java",
        types.SimpleNamespace(language=object),
    )

    validator = verification.PatchQualityVerifier(pathlib.Path("."), None)
    result = validator.verify_syntax("Demo.java", "class Demo {")

    assert result.status == "error"
    assert result.errors


def test_java_syntax_validator_returns_unavailable_when_dependency_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        verification,
        "tree_sitter_import_error",
        ImportError("missing dependency: tree_sitter"),
    )

    validator = verification.PatchQualityVerifier(pathlib.Path("."), None)
    result = validator.verify_syntax("Demo.java", "class Demo {}")

    assert result.status == "unavailable"
    assert "tree-sitter" in result.message
