"""模拟 Semgrep JSON，核对不完整证据的整次拒绝及应用侧稳定身份。"""

from __future__ import annotations

import pathlib
from unittest import mock

import pydantic
import pytest

from autopatch_j.core.project import source_text
from autopatch_j.core.repair import evidence as scan_evidence
from autopatch_j.scanners import models
from autopatch_j.scanners.semgrep import results as semgrep_results


def _raw_result(
    source: str, occurrence: int, path: str = "Demo.java"
) -> dict[str, object]:
    evidence = "weak()"
    offsets = [
        index
        for index in range(len(source))
        if source.startswith(evidence, index)
    ]
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


def _scan(
    tmp_path: pathlib.Path, source: str, results: list[dict[str, object]]
) -> models.ScanResult:
    with (tmp_path / "Demo.java").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        stream.write(source)
    return semgrep_results.build_semgrep_scan_result(
        payload={"results": results, "errors": []},
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )


def test_fingerprints_are_stable_across_result_order_and_distinguish_duplicates(
    tmp_path: pathlib.Path,
) -> None:
    source = (
        "class Demo {\n  void a() { weak(); }" "\n  void b() { weak(); }\n}\n"
    )
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
    ordered_fingerprints = [
        first_by_offset[offset] for offset in sorted(first_by_offset)
    ]
    assert ordered_fingerprints[0].endswith(":1")
    assert ordered_fingerprints[1].endswith(":2")


def test_fingerprint_does_not_change_when_only_preceding_lines_move(
    tmp_path: pathlib.Path,
) -> None:
    original = "class Demo {\n  void run() { weak(); }\n}\n"
    moved = "// unrelated\n" + original

    original_scan = _scan(tmp_path, original, [_raw_result(original, 0)])
    moved_scan = _scan(tmp_path, moved, [_raw_result(moved, 0)])

    assert (
        original_scan.findings[0].fingerprint
        == moved_scan.findings[0].fingerprint
    )
    assert original_scan.findings[0].region != moved_scan.findings[0].region


def test_finding_identity_round_trips_in_scan_artifact_schema(
    tmp_path: pathlib.Path,
) -> None:
    source = "class Demo { void run() { weak(); } }"
    scan = _scan(tmp_path, source, [_raw_result(source, 0)])

    adapter = pydantic.TypeAdapter(models.ScanResult)
    restored = adapter.validate_json(adapter.dump_json(scan))

    assert restored.findings[0].identity == scan.findings[0].identity
    assert restored == scan


def test_incomplete_scan_artifact_schema_is_rejected() -> None:
    with pytest.raises(pydantic.ValidationError):
        pydantic.TypeAdapter(models.ScanResult).validate_python(
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


def test_semgrep_errors_discard_otherwise_valid_findings(
    tmp_path: pathlib.Path,
) -> None:
    source = "class Demo { void run() { weak(); } }"
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    result = semgrep_results.build_semgrep_scan_result(
        payload={
            "results": [_raw_result(source, 0)],
            "errors": [{"message": "Java parse error"}],
        },
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert not result.findings
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
    tmp_path: pathlib.Path,
    payload: dict[str, object],
) -> None:
    result = semgrep_results.build_semgrep_scan_result(
        payload=payload,
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert not result.findings
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
    tmp_path: pathlib.Path,
    mutate,
) -> None:
    source = "class Demo { void run() { weak(); } }"
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")
    malformed = _raw_result(source, 0)
    mutate(malformed)

    result = semgrep_results.build_semgrep_scan_result(
        payload={"results": [_raw_result(source, 0), malformed], "errors": []},
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert not result.findings


@pytest.mark.parametrize(
    "finding_path",
    [
        ".autopatch-j/StateOnly.java",
        "StateAlias.java",
    ],
)
def test_state_finding_path_discards_entire_scan_without_exposing_source(
    tmp_path: pathlib.Path,
    finding_path: str,
) -> None:
    safe_source = "class Demo { void run() { weak(); } }"
    state_source = (
        "class StateOnly { String value ="
        ' "STATE_FINDING_SECRET"; '
        "void run() { weak(); } }"
    )
    (tmp_path / "Demo.java").write_text(safe_source, encoding="utf-8")
    state_file = tmp_path / ".autopatch-j" / "StateOnly.java"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(state_source, encoding="utf-8")
    (tmp_path / "StateAlias.java").symlink_to(state_file)

    result = semgrep_results.build_semgrep_scan_result(
        payload={
            "results": [
                _raw_result(safe_source, 0),
                _raw_result(state_source, 0, path=finding_path),
            ],
            "errors": [],
        },
        repo_root=tmp_path,
        scope=["Demo.java"],
        targets=["Demo.java"],
    )

    assert result.status == "error"
    assert not result.findings
    assert "不属于源码范围" in result.message
    assert "STATE_FINDING_SECRET" not in result.message


def test_safe_source_symlink_finding_is_canonicalized(
    tmp_path: pathlib.Path,
) -> None:
    source = "class SafeSource { void run() { weak(); } }"
    source_file = tmp_path / "src" / "SafeSource.java"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(source, encoding="utf-8")
    (tmp_path / "SafeAlias.java").symlink_to(source_file)

    result = semgrep_results.build_semgrep_scan_result(
        payload={
            "results": [_raw_result(source, 0, path="SafeAlias.java")],
            "errors": [],
        },
        repo_root=tmp_path,
        scope=["SafeAlias.java"],
        targets=["src/SafeSource.java"],
    )

    assert result.status == "ok"
    assert len(result.findings) == 1
    assert result.findings[0].path == "src/SafeSource.java"
    assert "weak()" in result.findings[0].snippet


@pytest.mark.parametrize(
    "encoding,newline",
    [("utf-8", "\n"), ("utf-8-sig", "\r\n"), ("gbk", "\r\n")],
)
def test_normalization_reuses_one_lossless_source_per_file(
    tmp_path, monkeypatch, encoding, newline
):
    content = newline.join(
        [
            "class Demo {",
            *[f"  void f{i}() {{ weak(); }} // 中文" for i in range(50)],
            "}",
        ]
    )
    raw = content.encode(encoding)
    path = tmp_path / "Demo.java"
    path.write_bytes(raw)
    items = []
    cursor = 0
    for _ in range(50):
        start = raw.index(b"weak()", cursor)
        end = start + 6
        line = raw.count(b"\n", 0, start) + 1
        column = start - raw.rfind(b"\n", 0, start)
        items.append(
            {
                "check_id": "test.rule",
                "path": "Demo.java",
                "start": {"line": line, "col": column, "offset": start},
                "end": {"line": line, "col": column + 6, "offset": end},
            }
        )
        cursor = end
    reads = []
    decodes = []
    original_read = pathlib.Path.read_bytes
    original_decode = source_text.decode

    def read(source):
        if source == path:
            reads.append(source)
        return original_read(source)

    def decode(data, source):
        decodes.append(source)
        return original_decode(data, source)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    monkeypatch.setattr(source_text, "decode", decode)
    result = semgrep_results.build_semgrep_scan_result(
        {"results": items, "errors": []},
        tmp_path,
        ["Demo.java"],
        ["Demo.java"],
    )
    assert result.status == "ok"
    assert len(result.findings) == 50
    assert len(reads) == len(decodes) == 1
    assert [item.snippet for item in result.findings] == [
        f"void f{i}() {{ weak(); }} // 中文" for i in range(50)
    ]
    old_fingerprints = [item.fingerprint for item in result.findings]
    path.write_bytes(raw.replace(b"weak()", b"safe()"))
    again = semgrep_results.build_semgrep_scan_result(
        {"results": items, "errors": []},
        tmp_path,
        ["Demo.java"],
        ["Demo.java"],
    )
    assert again.status == "ok"
    assert len(reads) == len(decodes) == 2
    assert all("safe()" in item.snippet for item in again.findings)
    assert [item.fingerprint for item in again.findings] != old_fingerprints


def test_evidence_scan_keeps_pre_and_post_reads_separate(tmp_path, monkeypatch):
    content = "class Demo { void run() { weak(); } }"
    path = tmp_path / "Demo.java"
    path.write_bytes(content.encode())
    reads = []
    original = pathlib.Path.read_bytes

    def read(source):
        if source == path:
            reads.append(source)
        return original(source)

    def scan(root, targets):
        return semgrep_results.build_semgrep_scan_result(
            {"results": [_raw_result(content, 0)], "errors": []},
            root,
            targets,
            targets,
        )

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    scanner = mock.Mock()
    scanner.scan.side_effect = scan
    registry = scan_evidence.EvidenceService(tmp_path, scanner)
    record = registry.scan(("Demo.java",))
    assert len(record.result.findings) == 1
    assert len(reads) == 3


def test_second_source_read_failure_discards_all_normalized_findings(
    tmp_path, monkeypatch
):
    content = "class Demo { void run() { weak(); } }"
    for name in ("Demo.java", "Other.java"):
        (tmp_path / name).write_bytes(content.encode())
    original = pathlib.Path.read_bytes

    def read(path):
        if path.name == "Other.java":
            raise PermissionError("source denied")
        return original(path)

    monkeypatch.setattr(pathlib.Path, "read_bytes", read)
    result = semgrep_results.build_semgrep_scan_result(
        {
            "results": [
                _raw_result(content, 0),
                _raw_result(content, 0, "Other.java"),
            ],
            "errors": [],
        },
        tmp_path,
        ["."],
        ["Demo.java", "Other.java"],
    )
    assert result.status == "error"
    assert result.findings == []
    assert "source denied" in result.message
