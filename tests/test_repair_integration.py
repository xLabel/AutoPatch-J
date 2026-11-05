"""贯通原生 SDK、扫描证据、完整提案、人工应用与验证，替代仅外部扫描边界。"""

from unittest import mock

from autopatch_j.agent import result
from autopatch_j.cli import input_router
from autopatch_j.cli import render
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.core import finding
from autopatch_j.scanners import models
from tests import sdk_fakes


def test_scan_evidence_proposal_review_and_apply(tmp_path, sdk_scenario):
    source = "class A { int n = 1; }"
    (tmp_path / "A.java").write_text(source)
    start = source.index("n = 1")
    candidate = models.Finding(
        "apj-v1:" + "a" * 64 + ":1",
        "demo.rule",
        "A.java",
        finding.SourceRegion(1, start + 1, 1, start + 6, start, start + 5),
        "WARNING",
        "用户确认默认值应为 2",
        snippet="n = 1",
    )
    detail = sdk_fakes.Reply(tool="get_finding_detail", finish="tool_calls")
    proposal = sdk_fakes.Reply(
        tool="propose_patch",
        arguments={
            "edits": [
                {
                    "path": "A.java",
                    "replacements": [
                        {"old_text": "n = 1", "new_text": "n = 2"}
                    ],
                }
            ],
            "rationale": "用户确认默认值应为 2",
        },
        finish="tool_calls",
    )
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="scan_java",
                arguments={"paths": ["A.java"]},
                finish="tool_calls",
            ),
            detail,
            proposal,
            sdk_fakes.Reply(text="证据已核对，请审核。"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    scanner = mock.MagicMock()
    scanner.scan.side_effect = [
        models.ScanResult(
            "semgrep",
            ["A.java"],
            ["A.java"],
            "ok",
            "scan evidence",
            [candidate],
        ),
        models.ScanResult(
            "semgrep", ["A.java"], ["A.java"], "ok", "rescan evidence", []
        ),
    ]
    runtime.evidence.scanner = scanner
    runtime.repair.scanner = scanner

    def bind_returned_handle():
        if len(scenario.requests) >= 2:
            artifact = next(runtime.evidence.directory.glob("*.json"))
            handle = f"{artifact.stem}:F1"
            detail.arguments["finding_handle"] = handle
            proposal.arguments["finding_handle"] = handle

    scenario.before_reply = bind_returned_handle
    try:
        initial = runtime.repair.store.load()
        started = runtime.repair.commit(
            initial, runtime.repair.start(initial, "默认值修复", ("A.java",))
        )
        runtime.repair.approve_scope(started)
        outcome = runtime.agent.run(
            "扫描、核查当前默认值，并准备改为 2 的完整提案。"
        )
        assert outcome.status is result.RunStatus.COMPLETED
        pending = runtime.repair.store.load()
        assert (
            pending.proposal is not None and pending.proposal.target is not None
        )
        assert pending.proposal.target.identity == candidate.identity
        assert (tmp_path / "A.java").read_text() == source
        applied = runtime.repair.apply(pending)
        assert applied.status == "applied"
        assert applied.checks[-1].status == "passed"
        assert applied.checks[0].status == "unverified"
        assert (tmp_path / "A.java").read_text() == "class A { int n = 2; }"
        assert scanner.scan.call_count == 2
        assert len(scenario.requests) == 4
    finally:
        runtime.close()


def test_allow_continues_preparation_without_an_extra_user_turn(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="propose_patch",
                arguments={
                    "edits": [
                        {
                            "path": "A.java",
                            "replacements": [
                                {"old_text": "n = 1", "new_text": "n = 2"}
                            ],
                        }
                    ],
                    "rationale": "默认值修复",
                },
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="请审核"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        initial = runtime.repair.store.load()
        requested = runtime.repair.commit(
            initial, runtime.repair.start(initial, "修复数值", ("A.java",))
        )
        router = input_router.UserInputRouter(runtime, render.CliRenderer())
        router.handle_input("allow", requested)
        assert runtime.repair.store.load().status == "pending"
        assert len(scenario.requests) == 2
        assert b"n = 1" in (tmp_path / "A.java").read_bytes()
    finally:
        runtime.close()


def test_scan_failure_is_reported_even_if_model_answer_omits_it(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A {}")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="scan_java",
                arguments={"paths": ["A.java"]},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="模型没有提及扫描失败。"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    scanner = mock.MagicMock()
    scanner.scan.return_value = models.ScanResult(
        "semgrep", ["A.java"], ["A.java"], "error", "runtime unavailable"
    )
    runtime.evidence.scanner = scanner
    notices = []
    try:
        runtime.agent.run(
            "扫描 A.java",
            on_observation=lambda message, summary: notices.append(message),
        )
        assert any("静态扫描失败" in message for message in notices)
        assert not list(runtime.evidence.directory.glob("*.json"))
    finally:
        runtime.close()


def test_redundant_scope_request_guides_sdk_to_proposal_without_new_approval(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="request_write_scope",
                arguments={"paths": ["A.java"]},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(
                tool="propose_patch",
                arguments={
                    "edits": [
                        {
                            "path": "A.java",
                            "replacements": [
                                {"old_text": "n = 1", "new_text": "n = 2"}
                            ],
                        }
                    ],
                    "rationale": "修复默认值",
                },
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="请审核"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        initial = runtime.repair.store.load()
        requested = runtime.repair.commit(
            initial, runtime.repair.start(initial, "修复数值", ("A.java",))
        )
        runtime.repair.approve_scope(requested)
        assert (
            runtime.agent.run("继续准备完整提案").status
            is result.RunStatus.COMPLETED
        )
        state = runtime.repair.store.load()
        assert not state.requested_paths
        assert state.proposal is not None
        assert any(
            item.get("role") == "tool"
            and "无需再次 allow" in item.get("content", "")
            for item in scenario.requests[1]["messages"]
        )
    finally:
        runtime.close()
