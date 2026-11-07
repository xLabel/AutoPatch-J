"""有序编辑后的目标足迹、辅助文件和真实扫描证据边界。"""

from unittest import mock

import pytest

from autopatch_j.core import errors
from autopatch_j.core import finding
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.scanners import models as scan_models


def scanner_result(findings=(), status="ok"):
    return scan_models.ScanResult(
        "semgrep",
        ["A.java"],
        ["A.java"],
        status,
        "scan evidence",
        list(findings),
    )


def warning(source, text="value.get()", check_id="optional-check"):
    start = source.index(text)
    prefix = source[:start]
    line = prefix.count("\n") + 1
    column = len(prefix.rsplit("\n", 1)[-1]) + 1
    region = finding.SourceRegion(
        line,
        column,
        line,
        column + len(text),
        len(prefix.encode()),
        len((prefix + text).encode()),
    )
    return scan_models.Finding(
        "apj-v1:" + "a" * 64 + ":1",
        check_id,
        "A.java",
        region,
        "WARNING",
        "candidate",
        snippet=text,
    )


def prepared_task(root, scanner, source):
    (root / "A.java").write_text(source, encoding="utf-8")
    repair = service.RepairService(root, scanner)
    initial = repair.store.load()
    current = repair.commit(
        initial, repair.start(initial, "修复 Optional 缺陷", ("A.java",))
    )
    return repair, repair.approve_scope(current)


@pytest.mark.parametrize(
    "remaining,expected", [(True, "failed"), (False, "passed")]
)
def test_mapped_target_ignores_findings_outside_its_footprint(
    tmp_path, remaining, expected
):
    source = (
        "class A {\n  int padding;\n"
        "  Object f(java.util.Optional<Object> value) {\n"
        "    return value.get(); }\n}\n"
    )
    original = warning(source)
    scanner = mock.MagicMock()
    scanner.scan.return_value = scanner_result([original])
    repair, state = prepared_task(tmp_path, scanner, source)
    record = evidence.EvidenceService(tmp_path, scanner).scan(("A.java",))
    target, _ = evidence.EvidenceService(tmp_path, scanner).finding(
        f"{record.id}:F1"
    )
    replacement = "value.orElseThrow()"
    changes = models.FileEdit(
        path="A.java",
        replacements=(
            models.Replacement(
                old_text="  int padding;",
                new_text="  int padding;\n  int extra;",
            ),
            models.Replacement(old_text="value.get()", new_text=replacement),
        ),
    )
    state = repair.commit(
        state, repair.prepare(state, (changes,), "完善缺省行为", target)
    )
    updated = source.replace(
        "  int padding;", "  int padding;\n  int extra;"
    ).replace("value.get()", replacement)
    shifted = warning(updated, replacement)
    unrelated = warning(updated, "int padding;")
    scanner.scan.return_value = scanner_result(
        [shifted, unrelated] if remaining else [unrelated]
    )
    result = repair.apply(state)
    assert result.status == "applied"
    assert result.checks[-1].status == expected
    assert result.checks[0].status == "unverified"
    assert (tmp_path / "A.java").read_text() == updated
    assert repair.store.load().status == "applied"


def test_helper_file_fix_does_not_have_to_overlap_finding(tmp_path):
    source = "class A { Object value; }"
    scanner = mock.MagicMock()
    scanner.scan.return_value = scanner_result(
        [warning(source, "Object value;")]
    )
    repair, state = prepared_task(tmp_path, scanner, source)
    (tmp_path / "Helper.java").write_text("class Helper { int n = 1; }")
    state = repair.commit(state, repair.request_scope(state, ("Helper.java",)))
    state = repair.approve_scope(state)
    evidence_store = evidence.EvidenceService(tmp_path, scanner)
    record = evidence_store.scan(("A.java",))
    target, _ = evidence_store.finding(f"{record.id}:F1")
    state = repair.commit(
        state,
        repair.prepare(
            state,
            (
                models.FileEdit(
                    path="Helper.java",
                    replacements=(
                        models.Replacement(old_text="n = 1", new_text="n = 2"),
                    ),
                ),
            ),
            "修复产生错误值的辅助方法",
            target,
        ),
    )
    scanner.scan.return_value = scanner_result([])
    assert repair.apply(state).checks[-1].status == "passed"


@pytest.mark.parametrize("failure", ["error", "exception"])
def test_scan_failure_after_write_never_reverses_applied_fact(
    tmp_path, failure
):
    source = "class A { int n = 1; }"
    scanner = mock.MagicMock()
    scanner.scan.return_value = scanner_result([warning(source, "n = 1")])
    repair, state = prepared_task(tmp_path, scanner, source)
    evidence_store = evidence.EvidenceService(tmp_path, scanner)
    record = evidence_store.scan(("A.java",))
    target, _ = evidence_store.finding(f"{record.id}:F1")
    state = repair.commit(
        state,
        repair.prepare(
            state,
            (
                models.FileEdit(
                    path="A.java",
                    replacements=(
                        models.Replacement(old_text="n = 1", new_text="n = 2"),
                    ),
                ),
            ),
            "fix",
            target,
        ),
    )
    if failure == "exception":
        scanner.scan.side_effect = OSError("scanner missing")
    else:
        scanner.scan.return_value = scanner_result([], "error")
    with pytest.raises(errors.OperationError, match="源码已应用"):
        repair.apply(state)
    assert not repair.store.load().checks
    assert "复扫失败" in (repair.store.load().last_apply_error or "")
    assert repair.store.load().status == "applied"
    assert b"n = 2" in (tmp_path / "A.java").read_bytes()


def test_scan_identity_is_not_rebound_by_newer_scan_and_stale_bytes_rejected(
    tmp_path,
):
    source = "class A { int n = 1; }"
    scanner = mock.MagicMock()
    scanner.scan.return_value = scanner_result([warning(source, "n = 1")])
    repair, state = prepared_task(tmp_path, scanner, source)
    evidence_store = evidence.EvidenceService(tmp_path, scanner)
    first = evidence_store.scan(("A.java",))
    scanner.scan.return_value = scanner_result([])
    second = evidence_store.scan(("A.java",))
    assert first.id != second.id
    target, item = evidence_store.finding(f"{first.id}:F1")
    assert item.identity == target.identity
    assert evidence_store.load(second.id).result.findings == []
    (tmp_path / "A.java").write_text("class A { int n = 3; }")
    with pytest.raises(models.RepairError, match="证据.*变化"):
        repair.prepare(
            state,
            (
                models.FileEdit(
                    path="A.java",
                    replacements=(
                        models.Replacement(old_text="n = 3", new_text="n = 2"),
                    ),
                ),
            ),
            "fix",
            target,
        )


def test_scan_failures_and_midscan_changes_do_not_register_artifacts(tmp_path):
    path = tmp_path / "A.java"
    path.write_text("class A {}")
    scanner = mock.MagicMock()
    scanner.scan.return_value = scanner_result([], "error")
    evidence_store = evidence.EvidenceService(tmp_path, scanner)
    with pytest.raises(errors.OperationError, match="扫描失败"):
        evidence_store.scan(("A.java",))
    assert not list(evidence_store.directory.iterdir())

    def race(*unused_args):
        path.write_text("class Changed {}")
        return scanner_result([])

    scanner.scan.side_effect = race
    with pytest.raises(models.RepairError, match="扫描期间"):
        evidence_store.scan(("A.java",))
    assert not list(evidence_store.directory.iterdir())


def test_false_positive_is_explicit_no_change_and_not_execution_failure(
    tmp_path,
):
    source = "class A { java.util.Random random = new java.util.Random(42); }"
    repair, state = prepared_task(tmp_path, None, source)
    assert state.issue_id is not None
    assessment = models.Assessment(
        issue_id=state.issue_id,
        outcome="no_change",
        reason="模拟用途要求可复现随机序列，非安全敏感用途。",
    )
    state = repair.commit(state, repair.assess(state, assessment))
    assert state.status == "assessed"
    assert state.proposal is None
    assert state.assessment is not None
    assert state.assessment.outcome == "no_change"
    assert (tmp_path / "A.java").read_text() == source


def test_rescan_cannot_overwrite_a_newer_task(tmp_path):
    source = "class A { int n = 1; }"
    scanner = mock.MagicMock()
    scanner.scan.return_value = scanner_result([warning(source, "n = 1")])
    repair, state = prepared_task(tmp_path, scanner, source)
    evidence_store = evidence.EvidenceService(tmp_path, scanner)
    record = evidence_store.scan(("A.java",))
    target, _ = evidence_store.finding(f"{record.id}:F1")
    change = models.FileEdit(
        path="A.java",
        replacements=(models.Replacement(old_text="n = 1", new_text="n = 2"),),
    )
    state = repair.commit(
        state, repair.prepare(state, (change,), "fix", target)
    )

    def next_task(*unused_args):
        other = service.RepairService(tmp_path)
        applied = other.store.load()
        other.commit(applied, other.start(applied, "另一个问题", ("A.java",)))
        return scanner_result([])

    scanner.scan.side_effect = next_task
    with pytest.raises(models.StateConflict) as caught:
        repair.apply(state)
    assert "验证结果未写入当前任务" in str(caught.value)
    assert "源码已应用" in str(caught.value)
    assert repair.store.load().goal == "另一个问题"
    assert repair.store.load().proposal is None
