"""Agent 协议、工具纠错和业务候选提交边界。"""

import asyncio
import json

import httpx2 as httpx
from pydantic_ai import exceptions
from pydantic_ai import messages
import pytest

from autopatch_j import config as autopatch_j_config
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.core.project import symbol_index
from autopatch_j.core.repair import models
from autopatch_j.llm import policy
from tests import agent_helpers
from tests import sdk_fakes


@pytest.mark.parametrize(
    "finish",
    [
        "length",
        "content_filter",
        "insufficient_system_resource",
        "aborted",
        "unknown",
        None,
    ],
)
@pytest.mark.parametrize("with_tool", [True, False])
def test_abnormal_finish_never_executes_tool_or_saves_round(
    tmp_path, sdk_scenario, finish, with_tool
):
    reply = sdk_fakes.Reply(
        text="半截回答",
        finish=finish,
        tool="write_memory" if with_tool else None,
        arguments={"file": "MEMORY.md", "content": "不能写"},
    )
    scenario = sdk_scenario([reply])
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        with pytest.raises(
            (
                policy.ModelFailure,
                exceptions.UnexpectedModelBehavior,
                exceptions.ModelAPIError,
            )
        ):
            runtime.agent.run("记录长期决定")
        assert not runtime.model_runtime.run(runtime.memory.paths())
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
        assert runtime.repair.store.load().status == "idle"
        assert len(scenario.requests) == 1
    finally:
        runtime.close()


def test_source_reasoning_and_unified_history_do_not_mutate_pending(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    (tmp_path / "B.java").write_text("class B {}")
    scenario = sdk_scenario(
        [
            agent_helpers.proposed_reply(),
            sdk_fakes.Reply(text="请审核"),
            sdk_fakes.Reply(
                tool="read_source_file",
                arguments={"path": "B.java"},
                finish="tool_calls",
                reasoning="读取关联证据",
                fragment_arguments=True,
            ),
            sdk_fakes.Reply(text="B 是空类，A 的提案仍待审。"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        agent_helpers.authorize_task(runtime)
        assert (
            runtime.agent.run("修复 A 的数值").status
            is result.RunStatus.COMPLETED
        )
        pending = runtime.repair.store.load()
        assert pending.proposal is not None
        assert (
            runtime.agent.run("解释关联的 @B.java").status
            is result.RunStatus.COMPLETED
        )
        assert runtime.repair.store.load() == pending
        assert pending.write_paths == ("A.java",)
        assert (tmp_path / "A.java").read_text() == "class A { int n = 1; }"
        wire = json.dumps(scenario.requests[2], ensure_ascii=False)
        assert "修复 A 的数值" in wire
        tools = {
            item["function"]["name"] for item in scenario.requests[0]["tools"]
        }
        assert {
            "scan_java",
            "search_symbols",
            "propose_patch",
            "read_memory",
        } <= tools
        assert "apply" not in tools and "revise_patch" not in tools
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert any(
            isinstance(part, messages.ThinkingPart)
            for item in saved.messages
            if isinstance(item, messages.ModelResponse)
            for part in item.parts
        )
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "A.java", "line": True},
        {"path": "A.java", "line": "2"},
        {"path": "A.java", "line": 1.5},
        {"path": "A.java", "line": 1, "unknown": 1},
    ],
)
def test_sdk_rejects_wrong_types_and_unknown_arguments(
    tmp_path, sdk_scenario, monkeypatch, arguments
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_source_context",
                arguments=arguments,
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="参数不合法"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    calls = []
    real = runtime.source_reader.read

    def read(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(runtime.source_reader, "read", read)
    try:
        assert runtime.agent.run("解释").status is result.RunStatus.COMPLETED
        assert not calls
        assert any(
            item.get("role") == "tool"
            and (
                "unknown" in item.get("content", "")
                or "valid integer" in item.get("content", "")
            )
            for item in scenario.requests[1]["messages"]
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("end", ["stop", "length", "cancel"])
def test_proposal_commits_only_after_eligible_end(tmp_path, sdk_scenario, end):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [
            agent_helpers.proposed_reply(),
            sdk_fakes.Reply(
                text="请审核", finish="stop" if end == "cancel" else end
            ),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        original = agent_helpers.authorize_task(runtime)
        if end == "cancel":

            async def cancel_second():
                if len(scenario.requests) == 1:
                    raise asyncio.CancelledError()

            scenario.before_response = cancel_second
        if end == "length":
            with pytest.raises(policy.ModelFailure):
                runtime.agent.run("修复数值")
            response = None
        else:
            response = runtime.agent.run("修复数值")
        state = runtime.repair.store.load()
        if end == "stop":
            assert response is not None
            assert response.status is result.RunStatus.COMPLETED
            assert state.status == "pending"
        else:
            if end == "cancel":
                assert response is not None
                assert response.status is result.RunStatus.CANCELLED
            assert state == original
            assert not runtime.model_runtime.run(
                runtime.conversations.load()
            ).messages
    finally:
        runtime.close()


def test_match_error_corrected_in_same_sdk_run(tmp_path, sdk_scenario):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [
            agent_helpers.proposed_reply("missing", "new"),
            agent_helpers.proposed_reply(),
            sdk_fakes.Reply(text="已纠正原文，请审核"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        agent_helpers.authorize_task(runtime)
        response = runtime.agent.run("修复")
        assert response.status is result.RunStatus.COMPLETED
        assert len(scenario.requests) == 3
        assert any(
            item.get("role") == "tool" and "匹配0次" in item.get("content", "")
            for item in scenario.requests[1]["messages"]
        )
        prepared = runtime.repair.store.load().proposal
        assert prepared is not None
        assert prepared.files[0].candidate == b"class A { int n = 2; }"
    finally:
        runtime.close()


def test_failed_revision_keeps_pending_and_completed_history(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [
            agent_helpers.proposed_reply(),
            sdk_fakes.Reply(text="第一版"),
            agent_helpers.proposed_reply(new="n = 3"),
            sdk_fakes.Reply(text="半截", finish="length"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        agent_helpers.authorize_task(runtime)
        runtime.agent.run("修复数值")
        pending = runtime.repair.store.load()
        with pytest.raises(policy.ModelFailure):
            runtime.agent.run("请改成 3")
        assert runtime.repair.store.load() == pending
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert (
            "请改成 3"
            not in messages.ModelMessagesTypeAdapter.dump_json(
                saved.messages
            ).decode()
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("tool", [False, True])
def test_native_correction_budget_stops_repeated_invalid_responses(
    tmp_path, sdk_scenario, tool
):
    reply = sdk_fakes.Reply(
        tool="read_source_file" if tool else None,
        arguments={"path": 23} if tool else {},
        finish="tool_calls" if tool else "stop",
    )
    scenario = sdk_scenario([reply, reply])
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        with pytest.raises(exceptions.UnexpectedModelBehavior):
            runtime.agent.run("读取源码")
        assert len(scenario.requests) == 2
        assert runtime.repair.store.load().status == "idle"
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
    finally:
        runtime.close()


def test_start_scope_and_proposal_use_one_agent_with_explicit_human_grant(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="start_repair",
                arguments={"goal": "修复数值", "write_paths": ["A.java"]},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="请批准 A.java 的写范围"),
            agent_helpers.proposed_reply(),
            sdk_fakes.Reply(text="请审核完整提案"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        runtime.agent.run("修复 A.java 的数值")
        requested = runtime.repair.store.load()
        assert requested.requested_paths == ("A.java",)
        assert not requested.write_paths and requested.proposal is None
        runtime.repair.approve_scope(requested)
        response = runtime.agent.run("继续准备提案")
        assert response.status is result.RunStatus.COMPLETED
        assert len(scenario.requests) == 4
        assert runtime.repair.store.load().status == "pending"
        assert b"n = 1" in (tmp_path / "A.java").read_bytes()
    finally:
        runtime.close()


def test_stale_business_commit_does_not_save_success_history(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario(
        [agent_helpers.proposed_reply(), sdk_fakes.Reply(text="待审核")]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        original = agent_helpers.authorize_task(runtime)

        def external_end():
            if len(scenario.requests) == 2:
                runtime.repair.end(original)

        scenario.before_reply = external_end
        with pytest.raises(models.StateConflict):
            runtime.agent.run("修复数值")
        assert runtime.repair.store.load().status == "idle"
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
    finally:
        runtime.close()


def test_provider_capacity_rejection_stops_without_retry_or_business_commit(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario([agent_helpers.proposed_reply()])
    normal = scenario.handle

    def reject_second(request):
        if scenario.requests:
            scenario.requests.append(json.loads(request.content))
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "context too long",
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded",
                    }
                },
            )
        return normal(request)

    scenario.handle = reject_second
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        original = agent_helpers.authorize_task(runtime)
        with pytest.raises(exceptions.ModelAPIError):
            runtime.agent.run("修复数值")
        assert len(scenario.requests) == 2
        assert runtime.repair.store.load() == original
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
    finally:
        runtime.close()


def test_runtime_assembly_failure_closes_client(
    tmp_path, sdk_scenario, monkeypatch
):
    resources = sdk_scenario([]).runtime()

    def fail(*args, **kwargs):
        raise OSError("cannot create index")

    monkeypatch.setattr(symbol_index, "SymbolIndex", fail)
    with pytest.raises(OSError, match="cannot create index"):
        project_runtime.build_project_runtime(
            tmp_path, autopatch_j_config.AppConfig(), lambda: resources
        )
    assert resources.closed
    assert resources.client is not None and resources.client.is_closed()


def test_cancel_waits_for_async_cleanup(tmp_path, sdk_scenario):
    del tmp_path
    resources = sdk_scenario([]).runtime()
    cleaned = []

    async def operation():
        try:
            asyncio.get_running_loop().call_soon(resources.cancel)
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.append(True)

    try:
        with pytest.raises(asyncio.CancelledError):
            resources.run(operation())
        assert cleaned == [True]
        assert resources.task is None
        assert resources.run(asyncio.sleep(0, result="usable")) == "usable"
    finally:
        resources.close()


def test_requirement_quote_can_be_corrected_with_native_budget(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A {}", encoding="utf-8")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="update_task_requirements",
                arguments={"additions": ["不得改动公开 API"], "removals": []},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(
                tool="update_task_requirements",
                arguments={"additions": ["保持公开方法签名"], "removals": []},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="已保留用户的原文要求"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        agent_helpers.authorize_task(runtime)
        outcome = runtime.agent.run("请修复，保持公开方法签名")
        assert outcome.status is result.RunStatus.COMPLETED
        assert runtime.repair.store.load().constraints == ("保持公开方法签名",)
        feedback = json.dumps(scenario.requests[1], ensure_ascii=False)
        assert "不得改动公开 API" in feedback and "user_request" in feedback
        assert "直接作为依据" in feedback
    finally:
        runtime.close()
