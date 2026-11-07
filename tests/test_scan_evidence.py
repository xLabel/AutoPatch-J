"""通过真实扫描快照和修复状态边界核对严格证据及正常推进。"""

import copy
import json
from unittest import mock

import pydantic
import pytest

from autopatch_j.core import errors
from autopatch_j.core import finding
from autopatch_j.core.repair import evidence
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.core.repair import store
from autopatch_j.scanners import models as scan_models
from autopatch_j.scanners.semgrep import results


@pytest.fixture(name="recorded_scan")
def recorded_scan_fixture(tmp_path):
    source = b"class A { int n = 1; }"
    (tmp_path / "A.java").write_bytes(source)
    offset = source.index(b"n = 1")
    normalized = results.build_semgrep_scan_result(
        {
            "errors": [],
            "results": [
                {
                    "check_id": "test.rule",
                    "path": "A.java",
                    "start": {"line": 1, "col": offset + 1, "offset": offset},
                    "end": {"line": 1, "col": offset + 6, "offset": offset + 5},
                    "extra": {"severity": "WARNING", "message": "candidate"},
                }
            ],
        },
        tmp_path,
        ["A.java"],
        ["A.java"],
    )
    assert normalized.status == "ok"
    scanner = mock.Mock()
    scanner.scan.return_value = normalized
    registry = evidence.EvidenceService(tmp_path, scanner)
    record = registry.scan(("A.java",))
    return registry, record, scanner


def test_scan_round_trip_and_finding_bound_state_progression(
    tmp_path, recorded_scan
):
    registry, record, scanner = recorded_scan
    assert registry.load(record.id).model_dump() == record.model_dump()
    target, candidate = registry.finding(f"{record.id}:F1")
    assert target.identity == candidate.identity
    repair = service.RepairService(tmp_path, scanner)
    state = repair.store.load()
    state = repair.commit(
        state, repair.start(state, "修复数值，保留类型", ("A.java",))
    )
    state = repair.approve_scope(state)
    state = repair.commit(
        state,
        repair.update_task_requirements(
            state,
            ("保留类型",),
            (),
            "修复数值，保留类型",
        ),
    )
    proposal = repair.prepare(
        state,
        (
            models.FileEdit(
                path="A.java",
                replacements=(
                    models.Replacement(old_text="n = 1", new_text="n = 2"),
                ),
            ),
        ),
        "修复数值",
        target,
    )
    assert models.RepairState.model_validate(proposal.model_dump()) == proposal
    assert (
        models.RepairState.model_validate_json(proposal.model_dump_json())
        == proposal
    )
    assert store.changed(proposal, constraints=("保留类型",)) == proposal
    state = repair.commit(state, proposal)
    assert repair.store.load() == state
    state = repair.commit(
        state,
        repair.update_task_requirements(
            state,
            ("不改类名",),
            ("保留类型",),
            "撤回保留类型，不改类名",
        ),
    )
    assert state.constraints == ("不改类名",)
    scanner.scan.return_value = scan_models.ScanResult(
        "semgrep", ["A.java"], ["A.java"], "ok", "clean"
    )
    applied = repair.apply(state)
    assert applied.status == "applied"
    assert applied.checks[-1].status == "passed"
    assert (tmp_path / "A.java").read_bytes() == b"class A { int n = 2; }"
    saved = repair.store.load()
    assert saved.proposal is not None and saved.proposal.target == target


@pytest.mark.parametrize("invalid", [True, "1", 1.5])
def test_region_constructor_and_real_snapshot_reject_non_integer(
    recorded_scan, invalid
):
    registry, record, _ = recorded_scan
    with pytest.raises(pydantic.ValidationError):
        finding.SourceRegion(
            **{
                "start_line": invalid,
                "start_column": 1,
                "end_line": 1,
                "end_column": 2,
                "start_offset": 0,
                "end_offset": 1,
            }
        )
    data = record.model_dump(mode="json")
    data["result"]["findings"][0]["region"]["start_line"] = invalid
    path = registry.directory / f"{record.id}.json"
    damaged = json.dumps(data).encode()
    path.write_bytes(damaged)
    with pytest.raises(errors.OperationError) as caught:
        registry.load(record.id)
    assert isinstance(caught.value.__cause__, pydantic.ValidationError)
    assert path.read_bytes() == damaged


@pytest.mark.parametrize(
    "location,value",
    [
        (("id",), "b" * 32),
        (("id",), "invalid"),
        (("source_hashes", "A.java"), "invalid"),
        (("source_hashes",), {}),
        (("result", "status"), "error"),
        (("result", "status"), "unknown"),
        (("result", "scope"), [1]),
        (("result", "targets"), "A.java"),
        (("result", "extra"), True),
        (("result", "findings", 0, "extra"), True),
        (("result", "findings", 0, "severity"), 3),
        (("result", "findings", 0, "fingerprint"), "invalid"),
        (("result", "findings", 0, "path"), "../A.java"),
        (("result", "findings", 0, "check_id"), " rule "),
        (("result", "findings", 0, "region", "extra"), True),
        (("result", "findings", 0, "region", "start_line"), 0),
        (("result", "findings", 0, "region", "end_offset"), -1),
    ],
)
def test_corrupt_scan_is_rejected_without_rewriting(
    recorded_scan, location, value
):
    registry, record, _ = recorded_scan
    data = record.model_dump(mode="json")
    parent = data
    for key in location[:-1]:
        parent = parent[key]
    parent[location[-1]] = value
    path = registry.directory / f"{record.id}.json"
    damaged = json.dumps(data).encode()
    path.write_bytes(damaged)
    with pytest.raises(errors.OperationError) as caught:
        registry.load(record.id)
    assert caught.value.__cause__ is not None
    assert path.read_bytes() == damaged


@pytest.mark.parametrize("field", ["scope", "targets", "findings"])
def test_python_scan_containers_are_not_coerced(recorded_scan, field):
    _, record, _ = recorded_scan
    data = record.result
    adapter = pydantic.TypeAdapter(scan_models.ScanResult)
    fields = copy.deepcopy(adapter.dump_python(data))
    fields[field] = tuple(fields[field])
    with pytest.raises(pydantic.ValidationError):
        adapter.validate_python(fields)


@pytest.mark.parametrize("invalid", [True, "1", 1.5])
def test_repair_store_rejects_invalid_nested_region(
    tmp_path, recorded_scan, invalid
):
    registry, record, scanner = recorded_scan
    target, _ = registry.finding(f"{record.id}:F1")
    repair = service.RepairService(tmp_path, scanner)
    state = repair.store.load()
    state = repair.commit(state, repair.start(state, "修复", ("A.java",)))
    state = repair.approve_scope(state)
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
            "修复",
            target,
        ),
    )
    data = state.model_dump(mode="json")
    data["proposal"]["target"]["identity"]["region"]["start_line"] = invalid
    damaged = json.dumps(data).encode()
    repair.store.path.write_bytes(damaged)
    with pytest.raises(errors.OperationError):
        repair.store.load()
    assert repair.store.path.read_bytes() == damaged
