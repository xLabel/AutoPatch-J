"""执行事实必须进入真实 SDK 请求；快照、失败和当前源码不能混淆。"""

import dataclasses

import pytest

from autopatch_j import config
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.core import errors
from autopatch_j.core.repair import models
from autopatch_j.core.repair import service
from autopatch_j.core.repair import status
from autopatch_j.core.repair import store
from tests import agent_helpers
from tests import sdk_fakes
from tests import test_repair


@pytest.mark.parametrize("check_status", ["failed", "unverified"])
def test_applied_evidence_survives_restart_and_enters_model_request(
    tmp_path, sdk_scenario, check_status
):
    repair, pending = test_repair.pending(tmp_path)
    repair.apply(pending)
    applied = repair.store.load()
    saved = store.changed(
        applied,
        checks=(
            models.Check(
                kind="target_rescan",
                status=check_status,
                message="RESCAN_EVIDENCE_MUST_REACH_MODEL",
            ),
        ),
    )
    repair.store.save(saved)
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(tool="get_repair_status", finish="tool_calls"),
            sdk_fakes.Reply(text="说明执行事实"),
        ]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        assert (
            project.agent.run("只解释上次验证结果").status
            is result.RunStatus.COMPLETED
        )
        projected = agent_helpers.request_binding(scenario.requests[0])
        assert projected["status"] == "applied"
        assert projected["checks"][0]["status"] == check_status
        assert projected["progress"][0]["status"] == "written"
        assert not projected["pending_commit"]
        assert projected["source_evidence"][0]["status"] == "current"
        tool_returns = [
            item["content"]
            for item in scenario.requests[1]["messages"]
            if item["role"] == "tool"
        ]
        assert any(
            "RESCAN_EVIDENCE_MUST_REACH_MODEL" in item for item in tool_returns
        )
        assert project.repair.store.load() == saved
        assert (tmp_path / "A.java").read_text() == "class A { int n = 2; }"
        assert "candidate" not in projected and "original" not in projected
    finally:
        project.close()


def test_working_proposal_is_marked_uncommitted_in_next_request(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [agent_helpers.proposed_reply(), sdk_fakes.Reply(text="请审核")]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        baseline = agent_helpers.authorize_task(project)
        observed = []

        def observe():
            observed.append(project.repair.store.load())

        scenario.before_reply = observe
        assert (
            project.agent.run("修复数值").status is result.RunStatus.COMPLETED
        )
        projected = agent_helpers.request_binding(scenario.requests[1])
        assert projected["status"] == "pending" and projected["pending_commit"]
        assert projected["base_version"] == baseline.version
        assert observed == [baseline, baseline]
        persisted = project.repair.store.load()
        assert persisted.proposal is not None
        assert persisted.proposal.id == projected["proposal_id"]
    finally:
        project.close()


def test_concurrent_change_stops_before_tool_and_followup_request(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario([agent_helpers.proposed_reply()])
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        baseline = agent_helpers.authorize_task(project)
        scenario.before_reply = lambda: project.repair.end(baseline)
        with pytest.raises(models.StateConflict):
            project.agent.run("修复")
        assert len(scenario.requests) == 1
        assert project.repair.store.load().status == "idle"
        assert not project.model_runtime.run(
            project.conversations.load()
        ).messages
        assert (tmp_path / "A.java").read_text() == "class A { int n = 1; }"
    finally:
        project.close()


@pytest.mark.parametrize("external", ["changed", "deleted"])
def test_old_validation_is_preserved_but_current_source_is_not_trusted(
    tmp_path, external
):
    repair, pending = test_repair.pending(tmp_path)
    repair.apply(pending)
    applied = repair.store.load()
    if external == "changed":
        (tmp_path / "A.java").write_text("class A { int external; }")
    else:
        (tmp_path / "A.java").unlink()
    if external == "deleted":
        with pytest.raises(errors.OperationError, match="无法核查当前源码"):
            status.describe(tmp_path, applied, applied)
        assert repair.store.load() == applied
        return
    projected = status.describe(tmp_path, applied, applied)
    assert projected.status == "applied"
    assert projected.checks == applied.checks
    assert projected.progress[0].status == "written"
    assert projected.source_evidence[0].status != "current"
    assert "不能作为当前通过" in projected.verification_notice
    assert repair.store.load() == applied


def test_rejection_reason_persists_and_is_cleared_by_revision(tmp_path):
    repair, pending = test_repair.pending(tmp_path)
    original = (tmp_path / "A.java").read_bytes()
    (tmp_path / "A.java").write_text("class A { int external; }")
    rejected = repair.apply(pending)
    saved = service.RepairService(tmp_path).store.load()
    assert saved.last_apply_error == rejected.message
    (tmp_path / "A.java").write_bytes(original)
    revised = repair.prepare(
        saved, (test_repair.edit("A.java", ("n = 1", "n = 3")),), "新修订"
    )
    assert revised.last_apply_error is None
    repair.apply(saved)
    assert repair.store.load().last_apply_error is None


def test_rejection_save_failure_is_not_claimed_persisted(tmp_path, monkeypatch):
    repair, pending = test_repair.pending(tmp_path)
    (tmp_path / "A.java").write_text("class A { int external; }")

    def unavailable(unused_state):
        raise OSError("storage unavailable")

    monkeypatch.setattr(repair.store, "save", unavailable)
    with pytest.raises(errors.OperationError, match="拒绝原因未保存"):
        repair.apply(pending)
    assert service.RepairService(tmp_path).store.load() == pending


def test_ending_task_does_not_invent_execution_history(tmp_path):
    repair, pending = test_repair.pending(tmp_path)
    repair.apply(pending)
    repair.end(repair.store.load())
    idle = repair.store.load()
    projected = status.describe(tmp_path, idle, idle)
    assert not projected.checks and not projected.progress
    assert projected.proposal_id is None
    assert "尚无已保存" in projected.verification_notice


def test_current_execution_evidence_is_reinjected_after_native_compaction(
    tmp_path, sdk_scenario
):
    repair, pending = test_repair.pending(tmp_path)
    repair.apply(pending)
    applied = repair.store.load()
    repair.store.save(
        store.changed(
            applied,
            checks=(
                models.Check(
                    kind="target_rescan",
                    status="failed",
                    message="CURRENT_RESCAN_FAILURE",
                ),
            ),
        )
    )
    scenario = sdk_scenario([sdk_fakes.Reply(text="解释当前失败")])
    scenario.summary_reply = sdk_fakes.Reply(
        text="较早调查摘要，不包含执行结果。"
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        assert project.model_runtime.profile is not None
        project.model_runtime.profile = dataclasses.replace(
            project.model_runtime.profile,
            context_window=14000,
            max_output_tokens=1000,
            provider_safety_tokens=1000,
        )
        saved = project.model_runtime.run(project.conversations.load())
        project.model_runtime.run(
            project.conversations.save(
                saved.summary, agent_helpers.large_history()
            )
        )
        assert (
            project.agent.run("解释当前验证").status
            is result.RunStatus.COMPLETED
        )
        assert any(not request.get("stream") for request in scenario.requests)
        main = [
            request for request in scenario.requests if request.get("stream")
        ]
        assert agent_helpers.request_binding(main[-1])["checks"][0] == {
            "kind": "target_rescan",
            "status": "failed",
            "message": "CURRENT_RESCAN_FAILURE",
        }
    finally:
        project.close()
