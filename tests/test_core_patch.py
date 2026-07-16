from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from autopatch_j.core.patching import (
    OldStringNotFoundError,
    OldStringNotUniqueError,
    SearchReplacePatchDraft,
    SearchReplacePatchEngine,
    SyntaxCheckResult,
    UnsafeSourceEncodingError,
)
from autopatch_j.scanners import FindingIdentity, SourceRegion


def _draft(
    file_path: str,
    old_string: str,
    new_string: str,
    diff: str,
    match_region: SourceRegion,
    target_finding: FindingIdentity | None = None,
) -> SearchReplacePatchDraft:
    return SearchReplacePatchDraft(
        file_path=file_path,
        old_string=old_string,
        new_string=new_string,
        diff=diff,
        match_region=match_region,
        validation=SyntaxCheckResult(status="ok", message=""),
        status="ok",
        message="",
        associated_finding_id="F1" if target_finding else None,
        source_scan_id="scan-1" if target_finding else None,
        target_finding=target_finding,
    )


def test_patch_lifecycle_returns_precise_regions(tmp_path: Path) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_text(
        'public class App {\n    void run() { System.out.println("old"); }\n}',
        encoding="utf-8",
    )
    engine = SearchReplacePatchEngine(tmp_path)
    old = 'System.out.println("old");'
    new = 'System.out.println("new");'

    build_result = engine.create_draft("App.java", old, new)

    assert build_result.match_region.start_line == 2
    assert build_result.match_region.start_column == 18
    assert build_result.match_region.end_offset - build_result.match_region.start_offset == len(old)

    apply_result = engine.apply_patch(
        _draft("App.java", old, new, build_result.diff, build_result.match_region)
    )

    assert apply_result.applied is True
    assert apply_result.changed_region is not None
    assert apply_result.changed_region.start_offset == build_result.match_region.start_offset
    assert "new" in java_file.read_text(encoding="utf-8")
    assert "old" not in java_file.read_text(encoding="utf-8")


def test_windows_crlf_matching_and_newline_preservation(tmp_path: Path) -> None:
    java_file = tmp_path / "Win.java"
    original = "public class Win {\r\n    public void test() {\r\n        return;\r\n    }\r\n}"
    java_file.write_bytes(original.encode("utf-8"))
    engine = SearchReplacePatchEngine(tmp_path)
    old_code = "    public void test() {\n        return;\n    }"
    new_code = "    public void test() {\n        // Fixed\n        return;\n    }"

    build_result = engine.create_draft("Win.java", old_code, new_code)
    apply_result = engine.apply_patch(
        _draft(
            "Win.java",
            old_code,
            new_code,
            build_result.diff,
            build_result.match_region,
        )
    )

    final_bytes = java_file.read_bytes()
    assert apply_result.applied is True
    assert b"// Fixed" in final_bytes
    assert b"\r\n" in final_bytes
    assert b"\n" not in final_bytes.replace(b"\r\n", b"")


def test_mixed_newlines_preserve_unmatched_bytes_and_rebase_later_draft(
    tmp_path: Path,
) -> None:
    java_file = tmp_path / "Mixed.java"
    original = b"first();\r\nsecond();\nthird();\r"
    java_file.write_bytes(original)
    engine = SearchReplacePatchEngine(tmp_path)
    applied_build = engine.create_draft(
        "Mixed.java",
        "second();",
        "fixed();\nextra();",
    )
    pending_build = engine.create_draft("Mixed.java", "third();", "fixedThird();")
    applied_draft = _draft(
        "Mixed.java",
        "second();",
        "fixed();\nextra();",
        applied_build.diff,
        applied_build.match_region,
    )
    pending_draft = _draft(
        "Mixed.java",
        "third();",
        "fixedThird();",
        pending_build.diff,
        pending_build.match_region,
    )

    apply_result = engine.apply_patch(applied_draft)

    assert apply_result.applied is True
    assert apply_result.source_region is not None
    assert apply_result.changed_region is not None
    match_start = original.index(b"second();")
    match_end = match_start + len(b"second();")
    replacement = b"fixed();\r\nextra();"
    expected = original[:match_start] + replacement + original[match_end:]
    final_bytes = java_file.read_bytes()
    assert final_bytes == expected
    assert apply_result.changed_region.start_offset == match_start
    assert apply_result.changed_region.end_offset == match_start + len(replacement)
    reported_delta = (
        apply_result.changed_region.end_offset
        - apply_result.changed_region.start_offset
        - (apply_result.source_region.end_offset - apply_result.source_region.start_offset)
    )
    assert reported_delta == len(final_bytes) - len(original)

    rebase_result = engine.rebase_draft(
        pending_draft,
        apply_result.source_region,
        apply_result.changed_region,
    )

    assert rebase_result.rebased is True
    assert rebase_result.build_result is not None
    assert rebase_result.build_result.match_region.start_offset == (
        pending_build.match_region.start_offset + reported_delta
    )
    rebased_draft = replace(
        pending_draft,
        diff=rebase_result.build_result.diff,
        match_region=rebase_result.build_result.match_region,
    )
    assert engine.apply_patch(rebased_draft).applied is True


def test_apply_patch_preserves_gbk_encoding_and_permission_mode(tmp_path: Path) -> None:
    java_file = tmp_path / "Legacy.java"
    content = 'public class Legacy {\n    String label = "中文";\n    String mode = "old";\n}\n'
    java_file.write_bytes(content.encode("gbk"))
    java_file.chmod(0o640)
    engine = SearchReplacePatchEngine(tmp_path)
    old_code = 'String mode = "old";'
    new_code = 'String mode = "new";'
    build_result = engine.create_draft("Legacy.java", old_code, new_code)

    result = engine.apply_patch(
        _draft(
            "Legacy.java",
            old_code,
            new_code,
            build_result.diff,
            build_result.match_region,
        )
    )

    assert result.applied is True
    assert 'String label = "中文";' in java_file.read_bytes().decode("gbk")
    assert 'String mode = "new";' in java_file.read_bytes().decode("gbk")
    assert stat.S_IMODE(java_file.stat().st_mode) == 0o640


def test_create_draft_rejects_lossy_source_decode(tmp_path: Path) -> None:
    binary_like = tmp_path / "Broken.java"
    original = b'class Broken {\n    byte[] bad = "\xff";\n}\xff'
    binary_like.write_bytes(original)
    engine = SearchReplacePatchEngine(tmp_path)

    with pytest.raises(UnsafeSourceEncodingError):
        engine.create_draft("Broken.java", "class Broken", "class Fixed")

    assert binary_like.read_bytes() == original


def test_encoding_failure_keeps_original_bytes(tmp_path: Path) -> None:
    java_file = tmp_path / "Legacy.java"
    original = 'class Legacy { String value = "旧"; }'.encode("gbk")
    java_file.write_bytes(original)
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("Legacy.java", '"旧"', '"🔒"')

    result = engine.apply_patch(
        _draft(
            "Legacy.java",
            '"旧"',
            '"🔒"',
            build_result.diff,
            build_result.match_region,
        )
    )

    assert result.applied is False
    assert result.error_code == "ENCODING_FAILED"
    assert java_file.read_bytes() == original


def test_fsync_failure_cleans_temp_and_keeps_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    java_file = tmp_path / "App.java"
    original = b'class App { String value = "old"; }'
    java_file.write_bytes(original)
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("App.java", '"old"', '"new"')
    monkeypatch.setattr(
        "autopatch_j.core.patching.search_replace.os.fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")),
    )

    result = engine.apply_patch(
        _draft(
            "App.java",
            '"old"',
            '"new"',
            build_result.diff,
            build_result.match_region,
        )
    )

    assert result.applied is False
    assert result.error_code == "TEMP_WRITE_FAILED"
    assert java_file.read_bytes() == original
    assert list(tmp_path.glob(".App.java.*.tmp")) == []


def test_replace_failure_cleans_temp_and_keeps_original(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    java_file = tmp_path / "App.java"
    original = b'class App { String value = "old"; }'
    java_file.write_bytes(original)
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("App.java", '"old"', '"new"')
    monkeypatch.setattr(
        "autopatch_j.core.patching.search_replace.os.replace",
        lambda _source, _target: (_ for _ in ()).throw(OSError("replace failed")),
    )

    result = engine.apply_patch(
        _draft(
            "App.java",
            '"old"',
            '"new"',
            build_result.diff,
            build_result.match_region,
        )
    )

    assert result.applied is False
    assert result.error_code == "ATOMIC_REPLACE_FAILED"
    assert java_file.read_bytes() == original
    assert list(tmp_path.glob(".App.java.*.tmp")) == []


def test_external_source_change_detected_before_final_compare_is_not_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_bytes(b'class App { String value = "old"; }')
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("App.java", '"old"', '"new"')
    real_fsync = os.fsync

    def change_source_after_fsync(file_descriptor: int) -> None:
        real_fsync(file_descriptor)
        java_file.write_bytes(b'class App { String value = "external"; }')

    monkeypatch.setattr(
        "autopatch_j.core.patching.search_replace.os.fsync",
        change_source_after_fsync,
    )

    result = engine.apply_patch(
        _draft(
            "App.java",
            '"old"',
            '"new"',
            build_result.diff,
            build_result.match_region,
        )
    )

    assert result.applied is False
    assert result.error_code == "SOURCE_CHANGED"
    assert b"external" in java_file.read_bytes()
    assert list(tmp_path.glob(".App.java.*.tmp")) == []


def test_apply_rejects_old_string_that_moved_outside_target_region(tmp_path: Path) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_text('class App { String value = "old"; }', encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("App.java", '"old"', '"new"')
    target = FindingIdentity(
        fingerprint=f"apj-v1:{'a' * 64}:1",
        check_id="demo.rule",
        path="App.java",
        region=build_result.match_region,
    )
    java_file.write_text(
        '// moved\nclass App { String value = "old"; }',
        encoding="utf-8",
    )

    result = engine.apply_patch(
        _draft(
            "App.java",
            '"old"',
            '"new"',
            build_result.diff,
            build_result.match_region,
            target,
        )
    )

    assert result.applied is False
    assert result.error_code == "SOURCE_CHANGED"
    assert '"old"' in java_file.read_text(encoding="utf-8")


def test_apply_rejects_unique_old_string_moved_from_bound_match_region(
    tmp_path: Path,
) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_text("old();\nkeep();\n", encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)
    build_result = engine.create_draft("App.java", "old();", "fixed();")
    draft = _draft(
        "App.java",
        "old();",
        "fixed();",
        build_result.diff,
        build_result.match_region,
    )
    java_file.write_text("keep();\nold();\n", encoding="utf-8")

    result = engine.apply_patch(draft)

    assert result.applied is False
    assert result.error_code == "SOURCE_CHANGED"
    assert java_file.read_text(encoding="utf-8") == "keep();\nold();\n"


def test_rebase_shifts_later_pending_match_and_target_regions(tmp_path: Path) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_text("first();\nsecond();\n", encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)
    first_build = engine.create_draft("App.java", "first();", "first();\nextra();")
    second_build = engine.create_draft("App.java", "second();", "fixedSecond();")
    second_target = FindingIdentity(
        fingerprint=f"apj-v1:{'b' * 64}:1",
        check_id="demo.second",
        path="App.java",
        region=second_build.match_region,
    )
    first_draft = _draft(
        "App.java",
        "first();",
        "first();\nextra();",
        first_build.diff,
        first_build.match_region,
    )
    second_draft = _draft(
        "App.java",
        "second();",
        "fixedSecond();",
        second_build.diff,
        second_build.match_region,
        second_target,
    )

    apply_result = engine.apply_patch(first_draft)
    assert apply_result.applied is True
    assert apply_result.source_region is not None
    assert apply_result.changed_region is not None

    rebase_result = engine.rebase_draft(
        second_draft,
        apply_result.source_region,
        apply_result.changed_region,
    )

    assert rebase_result.rebased is True
    assert rebase_result.build_result is not None
    assert rebase_result.rebased_target_finding is not None
    byte_delta = len("\nextra();".encode("utf-8"))
    assert rebase_result.build_result.match_region.start_offset == (
        second_build.match_region.start_offset + byte_delta
    )
    assert rebase_result.build_result.match_region.start_line == 3
    assert rebase_result.rebased_target_finding.fingerprint == second_target.fingerprint
    assert (
        rebase_result.rebased_target_finding.region
        == rebase_result.build_result.match_region
    )

    rebased_draft = replace(
        second_draft,
        diff=rebase_result.build_result.diff,
        match_region=rebase_result.build_result.match_region,
        target_finding=rebase_result.rebased_target_finding,
    )
    assert engine.apply_patch(rebased_draft).applied is True
    assert "fixedSecond();" in java_file.read_text(encoding="utf-8")


def test_rebase_keeps_earlier_pending_region_when_applied_edit_is_later(
    tmp_path: Path,
) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_text("first();\nsecond();\n", encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)
    first_build = engine.create_draft("App.java", "first();", "fixedFirst();")
    second_build = engine.create_draft("App.java", "second();", "longerSecondCall();")
    first_target = FindingIdentity(
        fingerprint=f"apj-v1:{'c' * 64}:1",
        check_id="demo.first",
        path="App.java",
        region=first_build.match_region,
    )
    first_draft = _draft(
        "App.java",
        "first();",
        "fixedFirst();",
        first_build.diff,
        first_build.match_region,
        first_target,
    )
    second_draft = _draft(
        "App.java",
        "second();",
        "longerSecondCall();",
        second_build.diff,
        second_build.match_region,
    )

    apply_result = engine.apply_patch(second_draft)
    assert apply_result.applied is True
    assert apply_result.source_region is not None
    assert apply_result.changed_region is not None

    rebase_result = engine.rebase_draft(
        first_draft,
        apply_result.source_region,
        apply_result.changed_region,
    )

    assert rebase_result.rebased is True
    assert rebase_result.build_result is not None
    assert rebase_result.build_result.match_region == first_build.match_region
    assert rebase_result.rebased_target_finding == first_target


def test_rebase_marks_overlapping_pending_binding_stale(tmp_path: Path) -> None:
    java_file = tmp_path / "App.java"
    java_file.write_text("unsafe().trim();\n", encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)
    applied_build = engine.create_draft("App.java", "unsafe()", "safe()")
    pending_build = engine.create_draft(
        "App.java",
        "unsafe().trim()",
        "safeTrimmed()",
    )
    applied_draft = _draft(
        "App.java",
        "unsafe()",
        "safe()",
        applied_build.diff,
        applied_build.match_region,
    )
    pending_draft = _draft(
        "App.java",
        "unsafe().trim()",
        "safeTrimmed()",
        pending_build.diff,
        pending_build.match_region,
    )

    apply_result = engine.apply_patch(applied_draft)
    assert apply_result.applied is True
    assert apply_result.source_region is not None
    assert apply_result.changed_region is not None

    rebase_result = engine.rebase_draft(
        pending_draft,
        apply_result.source_region,
        apply_result.changed_region,
    )

    assert rebase_result.rebased is False
    assert rebase_result.error_code == "STALE_DRAFT"
    assert rebase_result.build_result is None
    assert "相交" in rebase_result.message


def test_create_draft_failures(tmp_path: Path) -> None:
    java_file = tmp_path / "Test.java"
    java_file.write_text("code();\ncode();", encoding="utf-8")
    engine = SearchReplacePatchEngine(tmp_path)

    with pytest.raises(OldStringNotFoundError):
        engine.create_draft("Test.java", "non-existent", "...")
    with pytest.raises(OldStringNotUniqueError):
        engine.create_draft("Test.java", "code();", "...")


def test_path_traversal_defense(tmp_path: Path) -> None:
    engine = SearchReplacePatchEngine(tmp_path)
    with pytest.raises(PermissionError, match="安全风险拦截"):
        engine.create_draft("../../../etc/passwd", "any", "any")
