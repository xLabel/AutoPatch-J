"""真实文件、语法块和祖先规范，读取与写权限独立。"""

import subprocess
import sys

import pytest

from autopatch_j.core import errors
from autopatch_j.core.project import java_ast
from autopatch_j.core.project import repo_path
from autopatch_j.core.project import source_reader


def test_physical_ranges_and_truncation_can_be_continued(tmp_path):
    (tmp_path / "large.txt").write_bytes(
        "".join(f"line-{n}\n" for n in range(1, 501)).encode()
    )
    reader = source_reader.SourceReader(tmp_path)
    first = reader.read("large.txt", mode="file")
    assert (
        first.start_line,
        first.end_line,
        first.total_lines,
        first.truncated,
    ) == (1, 300, 500, True)
    next_part = reader.read("large.txt", 301, 500)
    assert next_part.code.startswith("line-301\n")
    assert next_part.end_line == 500 and not next_part.truncated


def test_java_block_and_lines_are_independent(tmp_path, monkeypatch):
    (tmp_path / "A.java").write_text(
        "class A {\n  int twice(int n) {\n    return n * 2;\n  }\n}\n"
    )
    reader = source_reader.SourceReader(tmp_path)
    result = reader.read("A.java", 3, mode="block")
    assert (result.start_line, result.end_line, result.mode) == (
        2,
        4,
        "java_block",
    )
    assert "return n * 2" in result.code

    def failed(unused_content):
        raise errors.OperationError("parser unavailable")

    monkeypatch.setattr(java_ast, "parse", failed)
    with pytest.raises(errors.OperationError, match="parser unavailable"):
        reader.read("A.java", 3, mode="block")
    lines = reader.read("A.java", 3, 3)
    assert lines.start_line == 3 and "return n * 2" in lines.code
    assert result.source_has_errors is False


def test_decode_metadata_and_errors_are_not_source_strings(tmp_path):
    (tmp_path / "bad.java").write_bytes(b"class A {}\xff\xff")
    reader = source_reader.SourceReader(tmp_path)
    with pytest.raises(UnicodeError, match="无损解码"):
        reader.read("bad.java")
    (tmp_path / "good.java").write_text("class A {}")
    with pytest.raises(FileNotFoundError):
        reader.read("missing.java")
    with pytest.raises(source_reader.SourceReadError):
        reader.read("good.java", 20)


def test_guidance_progressive_ancestors_and_safe_paths(tmp_path):
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root rule")
    (tmp_path / "src" / "AGENTS.md").write_text("java rule")
    (tmp_path / "src" / "nested" / "A.java").write_text("class A {}")
    reader = source_reader.SourceReader(tmp_path)
    assert [
        item.path for item in reader.read_guidance("src/nested/A.java")
    ] == [
        "AGENTS.md",
        "src/AGENTS.md",
    ]
    for path in ("../outside.txt", ".autopatch-j/repair.json"):
        with pytest.raises(repo_path.UnsafeRepoPathError):
            reader.read(path)
    state = tmp_path / ".autopatch-j"
    state.mkdir()
    (state / "private.txt").write_text("private")
    (tmp_path / "alias.txt").symlink_to(state / "private.txt")
    with pytest.raises(repo_path.UnsafeRepoPathError):
        reader.read("alias.txt")


def test_missing_required_parser_fails_module_import():
    script = """
import builtins
original = builtins.__import__
def missing(name, *args, **kwargs):
    if name in {"tree_sitter", "tree_sitter_java"}:
        raise ImportError("native parser unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = missing
from autopatch_j.core import errors
from autopatch_j.core.project import java_ast, java_ast
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "native parser unavailable" in completed.stderr


def test_tolerant_ast_returns_healthy_block_and_missing_block_is_explicit(
    tmp_path,
):
    (tmp_path / "A.java").write_text(
        "// outside block\nclass A {\n void broken() { int x = ; }\n"
        " void good() { unknown(); }\n}"
    )
    reader = source_reader.SourceReader(tmp_path)
    block = reader.read("A.java", 4, mode="block")
    assert "void good()" in block.code and block.source_has_errors
    assert block.syntax_issues
    with pytest.raises(source_reader.SourceReadError, match="未命中"):
        reader.read("A.java", 1, mode="block")
    assert reader.read("A.java", 1, 1).code.rstrip("\r\n") == "// outside block"


def test_non_java_block_request_does_not_implicitly_become_lines(tmp_path):
    (tmp_path / "notes.txt").write_text("notes")
    reader = source_reader.SourceReader(tmp_path)
    with pytest.raises(source_reader.SourceReadError, match="仅支持 Java"):
        reader.read("notes.txt", mode="block")
    assert reader.read("notes.txt", mode="file").code == "notes"
