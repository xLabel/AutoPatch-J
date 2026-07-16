from __future__ import annotations

from pathlib import Path

import pytest

from autopatch_j.scanners import ScanResult
from autopatch_j.scanners.semgrep import build_semgrep_scan_result


def _raw_result(source: str, occurrence: int, path: str = "Demo.java") -> dict[str, object]:
    evidence = "weak()"
    offsets = [index for index in range(len(source)) if source.startswith(evidence, index)]
    start_offset = offsets[occurrence]
    end_offset = start_offset + len(evidence)
    start_prefix = source[:start_offset]
    end_prefix = source[:end_offset]
    return {
        "check_id": "autopatch-j.java.security.weak-call",
        "path": path,
        "start": {
            "line": start_prefix.count("\n") + 1,
            "col": start_offset - start_prefix.rfind("\n"),
            "offset": start_offset,
        },
        "end": {
            "line": end_prefix.count("\n") + 1,
            "col": end_offset - end_prefix.rfind("\n"),
            "offset": end_offset,
        },
        "extra": {
            "severity": "ERROR",
            "message": "weak call",
            "lines": "requires login",
        },
    }


def _scan(tmp_path: Path, source: str, results: list[dict[str, object]]) -> ScanResult:
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")
    return build_semgrep_scan_result(
        payload={"results": results, "errors": []},
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )


def test_fingerprints_are_stable_across_result_order_and_distinguish_duplicates(
    tmp_path: Path,
) -> None:
    source = "class Demo {\n  void a() { weak(); }\n  void b() { weak(); }\n}\n"
    first_raw = _raw_result(source, 0)
    second_raw = _raw_result(source, 1)

    first_scan = _scan(tmp_path, source, [first_raw, second_raw])
    second_scan = _scan(tmp_path, source, [second_raw, first_raw])

    assert first_scan.status == "ok"
    first_by_offset = {
        finding.region.start_offset: finding.fingerprint
        for finding in first_scan.findings
    }
    second_by_offset = {
        finding.region.start_offset: finding.fingerprint
        for finding in second_scan.findings
    }
    assert first_by_offset == second_by_offset
    assert len(set(first_by_offset.values())) == 2
    ordered_fingerprints = [first_by_offset[offset] for offset in sorted(first_by_offset)]
    assert ordered_fingerprints[0].endswith(":1")
    assert ordered_fingerprints[1].endswith(":2")


def test_fingerprint_does_not_change_when_only_preceding_lines_move(tmp_path: Path) -> None:
    original = "class Demo {\n  void run() { weak(); }\n}\n"
    moved = "// unrelated\n" + original

    original_scan = _scan(tmp_path, original, [_raw_result(original, 0)])
    moved_scan = _scan(tmp_path, moved, [_raw_result(moved, 0)])

    assert original_scan.findings[0].fingerprint == moved_scan.findings[0].fingerprint
    assert original_scan.findings[0].region != moved_scan.findings[0].region


def test_finding_identity_round_trips_in_scan_artifact_schema(tmp_path: Path) -> None:
    source = "class Demo { void run() { weak(); } }"
    scan = _scan(tmp_path, source, [_raw_result(source, 0)])

    restored = ScanResult.from_dict(scan.to_dict())

    assert restored.findings[0].identity == scan.findings[0].identity
    assert restored.to_dict() == scan.to_dict()


def test_old_scan_artifact_schema_is_rejected() -> None:
    with pytest.raises(KeyError):
        ScanResult.from_dict(
            {
                "engine": "semgrep",
                "scope": ["Demo.java"],
                "targets": ["Demo.java"],
                "status": "ok",
                "message": "ok",
                "findings": [
                    {
                        "check_id": "demo.rule",
                        "path": "Demo.java",
                        "start_line": 1,
                        "end_line": 1,
                        "severity": "error",
                        "message": "legacy",
                    }
                ],
            }
        )


def test_semgrep_errors_discard_otherwise_valid_findings(tmp_path: Path) -> None:
    source = "class Demo { void run() { weak(); } }"
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    result = build_semgrep_scan_result(
        payload={
            "results": [_raw_result(source, 0)],
            "errors": [{"message": "Java parse error"}],
        },
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert result.findings == []
    assert "parse error" in result.message


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"results": []},
        {"errors": []},
    ],
)
def test_missing_required_semgrep_arrays_fail_closed(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    result = build_semgrep_scan_result(
        payload=payload,
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert result.findings == []
    assert "缺少必需字段" in result.message


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item["start"].pop("col"),
        lambda item: item["end"].__setitem__("offset", 10_000),
        lambda item: item.__setitem__("path", "../Outside.java"),
    ],
)
def test_malformed_finding_discards_entire_scan(
    tmp_path: Path,
    mutate,
) -> None:
    source = "class Demo { void run() { weak(); } }"
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")
    malformed = _raw_result(source, 0)
    mutate(malformed)

    result = build_semgrep_scan_result(
        payload={"results": [_raw_result(source, 0), malformed], "errors": []},
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert result.findings == []
