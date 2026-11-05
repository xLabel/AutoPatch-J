"""真实文件、语法块和祖先规范，读取与写权限独立。"""

import pytest

from autopatch_j.core.project import java_blocks
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


def test_java_block_has_actual_lines_and_independent_fallback(
    tmp_path, monkeypatch
):
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
    monkeypatch.setattr(
        java_blocks, "tree_sitter_import_error", ImportError("unavailable")
    )
    fallback = reader.read("A.java", 3, mode="block")
    assert fallback.start_line == 3 and fallback.mode == "fallback"
    assert fallback.notice == "unavailable"
    assert result.mode == "java_block" and result.notice is None


def test_decode_metadata_and_errors_are_not_source_strings(tmp_path):
    (tmp_path / "bad.java").write_bytes(b"class A {}\xff\xff")
    reader = source_reader.SourceReader(tmp_path)
    result = reader.read("bad.java")
    assert not result.lossless
    assert result.code.startswith("class A {}")
    with pytest.raises(FileNotFoundError):
        reader.read("missing.java")
    with pytest.raises(source_reader.SourceReadError):
        reader.read("bad.java", 20)


def test_guidance_progressive_ancestors_and_safe_paths(tmp_path):
    (tmp_path / "src" / "nested").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root rule")
    (tmp_path / "src" / "AGENTS.md").write_text("java rule")
    (tmp_path / "src" / "nested" / "A.java").write_text("class A {}")
    reader = source_reader.SourceReader(tmp_path)
    assert [item.path for item in reader.guidance("src/nested/A.java")] == [
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
