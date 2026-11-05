"""按完整提案与人工应用契约验证真实临时文件和持久状态。"""

import concurrent.futures
import stat
import subprocess
import sys

import pytest

from autopatch_j.core import finding
from autopatch_j.core.repair import checks
from autopatch_j.core.repair import files
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.core.repair import store


def edit(path, *replacements):
    return models.FileEdit(
        path=path,
        replacements=tuple(
            models.Replacement(old_text=old, new_text=new)
            for old, new in replacements
        ),
    )


def task(root, paths=("A.java",)):
    repair = service.RepairService(root)
    initial = repair.store.load()
    started = repair.commit(initial, repair.start(initial, "修复值", paths))
    return repair, repair.approve_scope(started)


def pending(root, edits=None):
    (root / "A.java").write_text("class A { int n = 1; }", encoding="utf-8")
    repair, state = task(root)
    prepared = repair.prepare(
        state, edits or (edit("A.java", ("n = 1", "n = 2")),), "修复数值"
    )
    return repair, repair.commit(state, prepared)


def test_ordered_replacements_prepare_full_candidate_without_writing(tmp_path):
    source = "class A { int n = 1; }"
    (tmp_path / "A.java").write_text(source, encoding="utf-8")
    repair, state = task(tmp_path)
    prepared = repair.prepare(
        state,
        (
            edit(
                "A.java",
                ("int n = 1;", "int count = 1;"),
                ("count = 1", "count = 2"),
            ),
        ),
        "rename and fix",
    )
    assert prepared.proposal is not None
    assert prepared.proposal.files[0].candidate == b"class A { int count = 2; }"
    assert (tmp_path / "A.java").read_text() == source
    assert repair.store.load() == state
    assert prepared.proposal.files[0].syntax.status == "passed"


@pytest.mark.parametrize(
    "replacements",
    [
        (("absent", "new"),),
        (("n", "x"),),
        (("n = 1", "n = 1"),),
        (("n = 1", "n = 2"), ("missing", "new")),
    ],
)
def test_failed_revision_keeps_original_proposal(tmp_path, replacements):
    repair, state = pending(tmp_path)
    with pytest.raises(models.RepairError):
        repair.prepare(state, (edit("A.java", *replacements),), "错误修订")
    assert repair.store.load() == state
    assert (tmp_path / "A.java").read_bytes() == b"class A { int n = 1; }"


@pytest.mark.parametrize(
    "encoding,bom,newline,ending",
    [
        ("utf-8", b"", "\n", ""),
        ("utf-8", b"\xef\xbb\xbf", "\r\n", "\r\n"),
        ("gbk", b"", "\r\n", ""),
    ],
)
def test_byte_preservation_roundtrip(tmp_path, encoding, bom, newline, ending):
    original = bom + (
        "// 中文" + newline + "class A { int n = 1; }" + ending
    ).encode(encoding)
    path = tmp_path / "A.java"
    path.write_bytes(original)
    repair, state = task(tmp_path)
    prepared = repair.prepare(
        state, (edit("A.java", ("n = 1", "n = 2")),), "fix"
    )
    state = repair.commit(state, prepared)
    restored = service.RepairService(tmp_path).store.load()
    assert restored == state
    assert repair.apply(restored).status == "applied"
    assert path.read_bytes() == original.replace(b"n = 1", b"n = 2")
    assert repair.store.load().status == "applied"
    with pytest.raises(models.StateConflict):
        repair.apply(state)


def test_multifile_preflight_rejects_before_any_write(tmp_path):
    for name in ("A.java", "B.java"):
        (tmp_path / name).write_text(f"class {name[0]} {{ int n = 1; }}")
    repair, state = task(tmp_path, ("A.java", "B.java"))
    state = repair.commit(
        state,
        repair.prepare(
            state,
            tuple(edit(name, ("n = 1", "n = 2")) for name in state.write_paths),
            "完整修复",
        ),
    )
    (tmp_path / "B.java").write_text("class B { int external = 3; }")
    result = repair.apply(state)
    assert result.status == "rejected"
    assert (tmp_path / "A.java").read_text() == "class A { int n = 1; }"
    assert repair.store.load() == state


def test_multifile_second_write_failure_stops_and_survives_restart(
    tmp_path, monkeypatch
):
    for name in ("A.java", "B.java"):
        (tmp_path / name).write_text(f"class {name[0]} {{ int n = 1; }}")
    repair, state = task(tmp_path, ("A.java", "B.java"))
    state = repair.commit(
        state,
        repair.prepare(
            state,
            tuple(edit(name, ("n = 1", "n = 2")) for name in state.write_paths),
            "完整修复",
        ),
    )
    real_write = files.atomic_write

    def fail_second(path, content, **kwargs):
        if path.name == "B.java":
            raise OSError("locked file")
        real_write(path, content, **kwargs)

    monkeypatch.setattr(files, "atomic_write", fail_second)
    result = repair.apply(state)
    assert result.status == "interrupted"
    assert [row.status for row in result.progress] == ["written", "not_written"]
    restarted = service.RepairService(tmp_path)
    persisted = restarted.store.load()
    assert persisted.status == "interrupted"
    assert "n = 2" in (tmp_path / "A.java").read_text()
    assert "n = 1" in (tmp_path / "B.java").read_text()
    with pytest.raises(models.RepairError):
        restarted.apply(persisted)
    restarted.end(persisted)
    assert "n = 2" in (tmp_path / "A.java").read_text()


def test_postwrite_save_failure_keeps_disk_blocked_and_reports_actual_write(
    tmp_path, monkeypatch
):
    repair, state = pending(tmp_path)
    real_save = repair.store.save

    def fail_after_write(value):
        if any(row.status == "written" for row in value.progress):
            raise OSError("disk full")
        real_save(value)

    monkeypatch.setattr(repair.store, "save", fail_after_write)
    result = repair.apply(state)
    assert result.status == "interrupted"
    assert result.progress[0].status == "written"
    persisted = service.RepairService(tmp_path).store.load()
    assert persisted.status == "applying"
    assert "等于提案结果" in repair.inspect(persisted)[0].message
    with pytest.raises(models.RepairError):
        repair.apply(persisted)


def test_checkpoint_failure_does_not_write_source(tmp_path, monkeypatch):
    repair, state = pending(tmp_path)
    monkeypatch.setattr(
        repair.store,
        "save",
        lambda value: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError):
        repair.apply(state)
    assert b"n = 1" in (tmp_path / "A.java").read_bytes()


def test_same_file_new_goal_cannot_replace_pending_task(tmp_path):
    repair, state = pending(tmp_path)
    with pytest.raises(models.RepairError):
        repair.start(state, "另一问题", ("A.java",))
    repair.end(state)
    cleared = repair.store.load()
    new_task = repair.start(cleared, "另一问题", ("A.java",))
    assert new_task.task_id != state.task_id
    assert not new_task.write_paths
    with pytest.raises(models.StateConflict):
        repair.commit(state, state)


def test_stale_run_and_simultaneous_apply_are_serialized(tmp_path):
    repair, state = pending(tmp_path)
    stale = repair.prepare(
        state, (edit("A.java", ("n = 1", "n = 3")),), "older run"
    )

    def apply_once():
        try:
            return service.RepairService(tmp_path).apply(state).status
        except models.StateConflict:
            return "conflict"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda unused: apply_once(), range(2))) == [
            "applied",
            "conflict",
        ]
    with pytest.raises(models.StateConflict):
        repair.commit(state, stale)


def test_invalid_state_is_not_silently_empty(tmp_path):
    repair = service.RepairService(tmp_path)
    repair.store.path.write_text('{"broken": true}')
    with pytest.raises(models.RepairError, match="状态损坏"):
        repair.store.load()


def test_unsafe_paths_duplicates_and_write_scope(tmp_path):
    repair, state = pending(tmp_path)
    (tmp_path / "B.java").write_text("class B {}")
    for edits in [
        (edit("../escape.java", ("a", "b")),),
        (edit(".autopatch-j/repair.json", ("a", "b")),),
        (edit("A.java", ("1", "2")), edit("./A.java", ("1", "3"))),
        (edit("B.java", ("class B", "class C")),),
    ]:
        with pytest.raises(ValueError):
            repair.prepare(state, edits, "bad")


def test_known_invalid_java_is_rejected(tmp_path):
    repair, state = pending(tmp_path)
    with pytest.raises(models.RepairError, match="语法"):
        repair.prepare(
            state, (edit("A.java", ("int n = 1;", "int = ;")),), "invalid"
        )
    assert repair.store.load() == state


@pytest.mark.parametrize("failure", ["fsync", "replace", "external_edit"])
def test_atomic_write_failure_preserves_original(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "A.java"
    original = b"class A { int n = 1; }"
    path.write_bytes(original)
    before = set(tmp_path.iterdir())
    real_fsync = files.os.fsync

    def fsync(descriptor):
        if failure == "fsync":
            raise OSError("fsync failed")
        if failure == "external_edit":
            path.write_bytes(b"external change")
        real_fsync(descriptor)

    def replace(*unused_args):
        raise OSError("replace failed")

    monkeypatch.setattr(files.os, "fsync", fsync)
    if failure == "replace":
        monkeypatch.setattr(files.os, "replace", replace)
    with pytest.raises((OSError, models.RepairError)):
        files.atomic_write(path, b"new bytes", expected=original)
    assert path.read_bytes() == (
        b"external change" if failure == "external_edit" else original
    )
    assert set(tmp_path.iterdir()) == before


def test_mixed_newlines_and_no_final_newline_diff(tmp_path):
    original = b"// line one\r\n// line two\nclass A { int n = 1; }"
    path = tmp_path / "A.java"
    path.write_bytes(original)
    repair, state = task(tmp_path)
    state = repair.commit(
        state,
        repair.prepare(state, (edit("A.java", ("n = 1", "n = 2")),), "fix"),
    )
    assert state.proposal is not None
    assert "\\ No newline at end of file" in state.proposal.files[0].diff
    assert "\n+class A" in state.proposal.files[0].diff
    assert repair.apply(state).status == "applied"
    assert path.read_bytes() == original.replace(b"n = 1", b"n = 2")


def test_unsafe_text_and_unencodable_revision_keep_source(tmp_path):
    path = tmp_path / "A.java"
    path.write_bytes("// 中文\nclass A { int n = 1; }".encode("gbk"))
    repair, state = task(tmp_path)
    with pytest.raises(UnicodeEncodeError):
        repair.prepare(
            state, (edit("A.java", ("n = 1", "n = 1; // 😀")),), "bad encoding"
        )
    path.write_bytes(b"\x00binary")
    with pytest.raises(models.RepairError, match="文本"):
        repair.prepare(state, (edit("A.java", ("binary", "text")),), "binary")
    assert path.read_bytes() == b"\x00binary"


def test_unavailable_syntax_remains_unverified_after_restart(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        checks, "tree_sitter_import_error", ImportError("missing")
    )
    repair, state = pending(tmp_path)
    assert state.proposal is not None
    assert state.proposal.files[0].syntax.status == "unverified"
    assert service.RepairService(repair.root).store.load() == state


def test_pending_revision_cannot_switch_target_binding(tmp_path):
    repair, state = pending(tmp_path)
    target = models.FindingTarget(
        handle="a" * 32 + ":F1",
        identity=finding.FindingIdentity(
            "apj-v1:" + "a" * 64 + ":1",
            "rule",
            "A.java",
            finding.SourceRegion(1, 1, 1, 6, 0, 5),
        ),
        source_sha256=files.digest((tmp_path / "A.java").read_bytes()),
    )
    with pytest.raises(models.RepairError, match="绑定"):
        repair.prepare(
            state, (edit("A.java", ("n = 1", "n = 2")),), "switch issue", target
        )


def test_windows_process_lock_excludes_another_process(tmp_path):
    repair = service.RepairService(tmp_path)
    code = """
import pathlib
import sys
import stat
import filelock
from autopatch_j.core.repair.service import RepairService
other = RepairService(pathlib.Path(sys.argv[1]))
try:
    other.store.lock.acquire(timeout=0)
except filelock.Timeout:
    print('blocked')
else:
    other.store.lock.release()
    print('unexpected acquisition')
"""
    with repair.store.locked():
        child = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    assert child.stdout.strip() == "blocked"
    assert repair.store.lock.lock_file == str(
        tmp_path / ".autopatch-j" / "repair.lock"
    )


def test_overlapping_matches_are_not_treated_as_unique(tmp_path):
    (tmp_path / "notes.txt").write_text("aaaa")
    repair, state = task(tmp_path, ("notes.txt",))
    with pytest.raises(models.RepairError, match="至少 2"):
        repair.prepare(state, (edit("notes.txt", ("aaa", "b")),), "ambiguous")
    assert (tmp_path / "notes.txt").read_text() == "aaaa"


def test_final_state_save_failure_reports_all_written_without_replay(
    tmp_path, monkeypatch
):
    repair, state = pending(tmp_path)
    real_save = repair.store.save

    def fail_final(value):
        if value.status == "applied":
            raise OSError("final save unavailable")
        real_save(value)

    monkeypatch.setattr(repair.store, "save", fail_final)
    outcome = repair.apply(state)
    assert outcome.status == "interrupted"
    assert outcome.progress[0].status == "written"
    restored = service.RepairService(tmp_path).store.load()
    assert restored.status == "applying"
    assert restored.progress[0].status == "written"
    assert not restored.checks
    with pytest.raises(models.RepairError):
        repair.apply(restored)
    assert b"n = 2" in (tmp_path / "A.java").read_bytes()


def test_prepared_permission_mode_is_preserved(tmp_path):
    repair, state = pending(tmp_path)
    path = tmp_path / "A.java"
    original_mode = stat.S_IMODE(path.stat().st_mode)
    assert repair.apply(state).status == "applied"
    assert stat.S_IMODE(path.stat().st_mode) == original_mode


def test_symlink_retargeting_cannot_escape_source_boundary(tmp_path):
    repair, state = pending(tmp_path)
    private = tmp_path / ".autopatch-j" / "private.java"
    private.write_text("class Private {}")
    path = tmp_path / "A.java"
    path.unlink()
    path.symlink_to(private)
    outcome = repair.apply(state)
    assert outcome.status == "rejected"
    assert private.read_text() == "class Private {}"


def test_temporary_constraints_end_with_application(tmp_path):
    repair, state = pending(tmp_path)
    state = repair.commit(
        state, store.changed(state, constraints=("仅当前修复使用这个写法",))
    )
    assert repair.apply(state).status == "applied"
    assert not repair.store.load().constraints


def test_scope_request_does_not_reopen_existing_authorization(tmp_path):
    repair, state = pending(tmp_path)
    with pytest.raises(models.RepairError, match="至少一个"):
        repair.request_scope(state, ())
    assert repair.request_scope(state, ("A.java",)) == state
    (tmp_path / "B.java").write_text("class B {}")
    expanded = repair.request_scope(state, ("A.java", "B.java"))
    assert expanded.write_paths == ("A.java",)
    assert expanded.requested_paths == ("B.java",)
    assert repair.request_scope(expanded, ("A.java",)) == expanded
