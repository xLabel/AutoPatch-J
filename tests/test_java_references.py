"""真实语法引用与 SDK 上下文，覆盖歧义、分页、字节和失败边界。"""

import hashlib
import json

import pytest

from autopatch_j import config
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.core.project import java_ast
from autopatch_j.core.project import java_paths
from autopatch_j.core.project import java_references
from autopatch_j.core.project import repo_path
from tests import sdk_fakes


def test_real_calls_exclude_comments_strings_and_similar_names(tmp_path):
    source = """class Service {
  Object run(Store store, Other other) {
    // store.load("comment-only");
    String example = "store.load(string-only)";
    other.preload(); other.Load();
    store.load("a"); other.load(1);
    return factory().load(key.trim());
  }
  Object factory() { return null; }
}"""
    (tmp_path / "Service.java").write_text(source)
    page = java_references.search(tmp_path, "load", (".",))
    assert page.complete and len(page.entries) == 3
    assert [entry.receiver for entry in page.entries] == [
        "store",
        "other",
        "factory()",
    ]
    assert all(
        entry.owner == "Service" and entry.enclosing_method == "run"
        for entry in page.entries
    )
    assert all(entry.kind == "call" for entry in page.entries)
    assert all(
        "comment-only" not in entry.excerpt
        and "string-only" not in entry.excerpt
        for entry in page.entries
    )
    assert len(java_references.search(tmp_path, "Load", (".",)).entries) == 1


def test_enum_constant_references_keep_lexical_owners_and_methods(tmp_path):
    (tmp_path / "Mode.java").write_text(
        "enum Mode {\n"
        " A { Object field = repo.load(); void run() { repo.load(); } },\n"
        " B { Object field = repo.load(); void run() { repo.load(); } };\n"
        " abstract void run();\n"
        " void shared() { repo.load(); }\n"
        "}",
        encoding="utf-8",
    )
    page = java_references.search(tmp_path, "load", (".",))
    assert page.complete and not page.warnings
    assert [
        (entry.owner, entry.enclosing_method) for entry in page.entries
    ] == [
        ("Mode.<anonymous@2:4>", None),
        ("Mode.<anonymous@2:4>", "run"),
        ("Mode.<anonymous@3:4>", None),
        ("Mode.<anonymous@3:4>", "run"),
        ("Mode", "shared"),
    ]


def test_constructor_references_generics_and_explicit_inheritance(tmp_path):
    (tmp_path / "Use.java").write_text("""
class Use extends pkg.Disk<String> implements Store<String> {
  Object make() {
    Object first = new pkg.Disk<String>();
    java.util.function.Supplier<pkg.Disk<String>> ref = pkg.Disk<String>::new;
    java.util.function.Function<String, String> load = this::load;
    return first;
  }
  String load(String key) { return key; }
}
interface Child extends Store<String> {}
""")
    disk = java_references.search(tmp_path, "Disk", (".",))
    assert [entry.kind for entry in disk.entries] == [
        "extends",
        "constructor",
        "constructor_reference",
    ]
    assert len(java_references.search(tmp_path, "Store", (".",)).entries) == 2
    load = java_references.search(tmp_path, "load", (".",))
    assert [entry.excerpt for entry in load.entries] == ["this::load"]
    assert load.entries[0].kind == "method_reference"
    assert not java_references.search(tmp_path, "new", (".",)).entries
    assert not java_references.search(tmp_path, "pkg.Disk", (".",)).entries


def test_pages_do_not_omit_or_repeat_unchanged_references(tmp_path):
    (tmp_path / "Many.java").write_text(
        "class Many { void run() {"
        + "\n".join("store.load();" for _ in range(45))
        + "} }"
    )
    first = java_references.search(tmp_path, "load", (".",))
    assert first.next_offset is not None
    second = java_references.search(tmp_path, "load", (".",), first.next_offset)
    assert second.next_offset is not None
    third = java_references.search(tmp_path, "load", (".",), second.next_offset)
    assert first.next_offset == 20 and second.next_offset == 40
    assert (
        len(third.entries) == 5 and third.next_offset is None and third.complete
    )
    assert not first.complete and not second.complete
    entries = [*first.entries, *second.entries, *third.entries]
    assert (
        len({(entry.path, entry.line, entry.column) for entry in entries}) == 45
    )


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "gbk"])
def test_original_hash_and_unicode_positions(tmp_path, encoding):
    content = "class 示例 { void 调用() { 仓库.读取(); } }"
    path = tmp_path / "Example.java"
    path.write_bytes(content.encode(encoding))
    entry = java_references.search(tmp_path, "读取", (".",)).entries[0]
    decoded = path.read_bytes().decode(
        "utf-8" if encoding.startswith("utf-8") else "gbk"
    )
    assert entry.column == decoded.index("仓库.读取") + 1
    assert entry.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert entry.receiver == "仓库" and entry.owner == "示例"


def test_whole_page_excerpt_budget_keeps_locations_and_followup(tmp_path):
    (tmp_path / "Long.java").write_text(
        'class Long { void run() { repo.load("'
        + "中" * 35000
        + '"); repo.load("next"); } }',
        encoding="utf-8",
    )
    page = java_references.search(tmp_path, "load", (".",))
    assert len(page.entries) == 2 and page.complete
    assert (
        sum(
            len(entry.excerpt)
            + len(entry.receiver or "")
            + len(entry.arguments or "")
            for entry in page.entries
        )
        == 30000
    )
    assert all(entry.excerpt_truncated for entry in page.entries)
    assert all(
        entry.source_sha256
        and entry.line == 1
        and entry.end_column > entry.column
        for entry in page.entries
    )
    assert page.entries[1].excerpt == ""


def test_parse_failure_is_partial_evidence_not_zero_references(tmp_path):
    (tmp_path / "Good.java").write_text(
        "class Good { void run() { repo.load(); } }"
    )
    (tmp_path / "Broken.java").write_text("class Broken { void run(")
    page = java_references.search(tmp_path, "load", (".",))
    assert len(page.entries) == 1 and not page.complete
    assert page.warnings[0].path == "Broken.java"
    assert page.next_offset is None


def test_missing_parser_and_empty_scope_cannot_claim_complete(
    tmp_path, monkeypatch
):
    (tmp_path / "A.java").write_text("class A {}")

    def failed(unused_path):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(java_ast, "read", failed)
    with pytest.raises(RuntimeError, match="parser unavailable"):
        java_references.search(tmp_path, "load", (".",))
    (tmp_path / "empty").mkdir()
    empty = java_references.search(tmp_path, "load", ("empty",))
    assert not empty.complete and "未执行" in empty.warnings[0].message


def test_ignored_directories_and_project_state_are_excluded(tmp_path):
    for folder in ("src", "target", ".autopatch-j"):
        path = tmp_path / folder
        path.mkdir()
        (path / "A.java").write_text("class A { void run() { repo.load(); } }")
    page = java_references.search(tmp_path, "load", (".",))
    assert [entry.path for entry in page.entries] == ["src/A.java"]
    with pytest.raises(repo_path.UnsafeRepoPathError):
        java_references.search(tmp_path, "load", (".autopatch-j/A.java",))


def test_symlinks_do_not_escape_repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "Outside.java"
    outside.write_text("class Outside { void run() { repo.load(); } }")
    (root / "Alias.java").symlink_to(outside)
    with pytest.raises(repo_path.UnsafeRepoPathError):
        java_references.search(root, "load", ("Alias.java",))


def test_source_changed_during_query_is_not_returned_as_current(
    tmp_path, monkeypatch
):
    path = tmp_path / "A.java"
    path.write_text("class A { void run() { repo.load(); } }")
    original = java_ast.read

    def changed(source_path):
        parsed = original(source_path)
        source_path.write_text("class Changed {}")
        return parsed

    monkeypatch.setattr(java_ast, "read", changed)
    with pytest.raises(java_references.ReferenceQueryError, match="源码已变化"):
        java_references.search(tmp_path, "load", (".",))


def test_lexical_ownership_respects_local_and_anonymous_type_boundaries(
    tmp_path,
):
    (tmp_path / "Outer.java").write_text("""class Outer {
      void enclosing() {
        class Local { Object field = repo.load(); }
        Runnable anonymous = new Runnable() {
          Object field = repo.load();
          public void run() { repo.load(); }
        };
        java.util.function.Supplier<Object> lambda = () -> repo.load();
      }
    }""")
    entries = java_references.search(tmp_path, "load", (".",)).entries
    assert len(entries) == 4
    assert entries[0].owner == "Outer.Local"
    assert entries[0].enclosing_method is None
    assert entries[1].owner.startswith("Outer.<anonymous@")
    assert entries[1].enclosing_method is None
    assert entries[2].owner == entries[1].owner
    assert entries[2].enclosing_method == "run"
    assert (
        entries[3].owner == "Outer"
        and entries[3].enclosing_method == "enclosing"
    )


def test_directory_read_error_cannot_be_reported_as_complete(
    tmp_path, monkeypatch
):
    def inaccessible(unused_path, *, onerror):
        onerror(PermissionError("directory unavailable"))
        return iter(())

    monkeypatch.setattr(java_paths.os, "walk", inaccessible)
    with pytest.raises(PermissionError, match="directory unavailable"):
        java_references.search(tmp_path, "load", (".",))


@pytest.mark.parametrize(
    "name, paths, offset",
    [
        ("", (".",), 0),
        ("load", (), 0),
        ("load", (".",), True),
        ("load", (".",), -1),
    ],
)
def test_invalid_query_is_rejected(tmp_path, name, paths, offset):
    with pytest.raises(java_references.ReferenceQueryError):
        java_references.search(tmp_path, name, paths, offset)


def test_reference_evidence_reaches_model_and_read_only_request_keeps_task_idle(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text(
        'class A { void run() { store.load("CONTRACT_EVIDENCE"); } }'
    )
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="search_java_references",
                arguments={"name": "load", "paths": ["."]},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(
                tool="read_source_block",
                arguments={"path": "A.java", "line": 1},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="只解释调用约定"),
        ]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        baseline = project.repair.store.load()
        assert (
            project.agent.run("只调查 load 的使用位置，不修复").status
            is result.RunStatus.COMPLETED
        )
        wire = scenario.requests[1]
        tool_results = [
            message["content"]
            for message in wire["messages"]
            if message["role"] == "tool"
        ]
        assert any(
            "CONTRACT_EVIDENCE" in content and "source_sha256" in content
            for content in tool_results
        )
        assert "CONTRACT_EVIDENCE" in json.dumps(scenario.requests[2])
        assert project.repair.store.load() == baseline
        assert (
            'store.load("CONTRACT_EVIDENCE")'
            in (tmp_path / "A.java").read_text()
        )
    finally:
        project.close()


def test_tolerant_reference_inside_error_source_is_marked(tmp_path):
    (tmp_path / "A.java").write_text(
        "class A { void broken() { int x = ; } void good() { repo.load(); } }"
    )
    page = java_references.search(tmp_path, "load", (".",))
    assert len(page.entries) == 1 and page.entries[0].source_has_errors
    assert not page.complete and page.warnings[0].path == "A.java"
