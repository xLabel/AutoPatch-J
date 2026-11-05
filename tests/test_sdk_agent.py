"""原生 SDK、真实 SQLite 与离线 HTTP 边界，不以替身代替业务状态转换。"""

import asyncio
import dataclasses
import json
import sqlite3

import httpx2 as httpx
from pydantic_ai import messages
from pydantic_ai_harness import memory as sdk_memory
import pytest

from autopatch_j.agent import result
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.core import conversations
from autopatch_j.core import memory
from autopatch_j.core.project import symbol_index
from autopatch_j.llm import context_window
from autopatch_j.llm import runtime as model_runtime
from tests import sdk_fakes


def authorize_task(runtime, goal="修复数值"):
    repair = runtime.repair
    initial = repair.store.load()
    active = repair.commit(initial, repair.start(initial, goal, ("A.java",)))
    return repair.approve_scope(active)


def proposed_reply(old="n = 1", new="n = 2"):
    return sdk_fakes.Reply(
        tool="propose_patch",
        arguments={
            "edits": [
                {
                    "path": "A.java",
                    "replacements": [{"old_text": old, "new_text": new}],
                }
            ],
            "rationale": "用户已确认数值需要修复",
        },
        finish="tool_calls",
    )


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
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        outcome = runtime.agent.run("记录长期决定")
        assert outcome.status is result.RunStatus.FAILED
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
            proposed_reply(),
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
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        authorize_task(runtime)
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
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    calls = []
    real = runtime.code_fetcher.read

    def read(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(runtime.code_fetcher, "read", read)
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
            proposed_reply(),
            sdk_fakes.Reply(
                text="请审核", finish="stop" if end == "cancel" else end
            ),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        original = authorize_task(runtime)
        if end == "cancel":

            async def cancel_second():
                if len(scenario.requests) == 1:
                    raise asyncio.CancelledError()

            scenario.before_response = cancel_second
        response = runtime.agent.run("修复数值")
        state = runtime.repair.store.load()
        if end == "stop":
            assert response.status is result.RunStatus.COMPLETED
            assert state.status == "pending"
        else:
            assert response.status is (
                result.RunStatus.CANCELLED
                if end == "cancel"
                else result.RunStatus.FAILED
            )
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
            proposed_reply("missing", "new"),
            proposed_reply(),
            sdk_fakes.Reply(text="已纠正原文，请审核"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        authorize_task(runtime)
        response = runtime.agent.run("修复")
        assert response.status is result.RunStatus.COMPLETED
        assert len(scenario.requests) == 3
        assert any(
            item.get("role") == "tool"
            and "匹配 0 次" in item.get("content", "")
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
            proposed_reply(),
            sdk_fakes.Reply(text="第一版"),
            proposed_reply(new="n = 3"),
            sdk_fakes.Reply(text="半截", finish="length"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        authorize_task(runtime)
        runtime.agent.run("修复数值")
        pending = runtime.repair.store.load()
        response = runtime.agent.run("请改成 3")
        assert response.status is result.RunStatus.FAILED
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


def test_sdk_default_request_limit_is_fifty(tmp_path, sdk_scenario):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_memory",
                arguments={"file": f"note-{index}.md"},
                finish="tool_calls",
            )
            for index in range(51)
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        for index in range(51):
            runtime.model_runtime.run(
                runtime.memory.store.write(
                    memory.PREFIX + f"note-{index}.md",
                    f"唯一内容 {index}",
                    expected_version=None,
                )
            )
        response = runtime.agent.run("逐个读取全部笔记")
        assert response.status is result.RunStatus.LIMITED
        assert len(scenario.requests) == 50
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
    finally:
        runtime.close()


def large_history():
    return [
        message
        for index in range(20)
        for message in (
            messages.ModelRequest(
                parts=[
                    messages.UserPromptPart(f"历史问题{index} " + "x" * 5000)
                ]
            ),
            messages.ModelResponse(
                parts=[messages.TextPart(f"历史回答{index} " + "y" * 5000)]
            ),
        )
    ]


@pytest.mark.parametrize("summary_finish", ["stop", "length", "unknown"])
def test_native_summary_keeps_current_request_and_failure_preserves_history(
    tmp_path, sdk_scenario, summary_finish
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="有效历史摘要", finish=summary_finish),
            sdk_fakes.Reply(text="最终回答"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    runtime.model_runtime.profile = dataclasses.replace(
        runtime.model_runtime.profile,
        context_window=40_000,
        max_output_tokens=1024,
        provider_safety_tokens=1024,
    )
    try:
        saved = runtime.model_runtime.run(runtime.conversations.load())
        history = large_history()
        runtime.model_runtime.run(
            runtime.conversations.save(saved.summary, history)
        )
        response = runtime.agent.run("本轮原文和必须保留的约束")
        assert scenario.requests[0]["stream"] is False
        persisted = runtime.model_runtime.run(runtime.conversations.load())
        if summary_finish == "stop":
            assert response.status is result.RunStatus.COMPLETED
            assert "本轮原文和必须保留的约束" in json.dumps(
                scenario.requests[1], ensure_ascii=False
            )
            assert len(persisted.messages) < len(history)
        else:
            assert response.status is result.RunStatus.FAILED
            assert len(scenario.requests) == 1
            assert persisted.messages == history
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
            proposed_reply(),
            sdk_fakes.Reply(text="请审核完整提案"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
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
    scenario = sdk_scenario([proposed_reply(), sdk_fakes.Reply(text="待审核")])
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        original = authorize_task(runtime)

        def external_end():
            if len(scenario.requests) == 2:
                runtime.repair.end(original)

        scenario.before_reply = external_end
        response = runtime.agent.run("修复数值")
        assert response.status is result.RunStatus.FAILED
        assert runtime.repair.store.load().status == "idle"
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
    finally:
        runtime.close()


@pytest.mark.parametrize("large_tail", [False, True])
def test_no_reclaim_summary_preserves_required_content_without_repeating(
    tmp_path, sdk_scenario, large_tail
):
    replies = (
        [sdk_fakes.Reply(text="过大的摘要" + "z" * 250_000)]
        if not large_tail
        else []
    ) + [
        sdk_fakes.Reply(
            tool="read_source_file",
            arguments={"path": "A.java"},
            finish="tool_calls",
        ),
        sdk_fakes.Reply(text="回答"),
    ]
    scenario = sdk_scenario(replies)
    (tmp_path / "A.java").write_text("class A {}")
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    runtime.model_runtime.profile = dataclasses.replace(
        runtime.model_runtime.profile,
        context_window=40_000,
        max_output_tokens=1024,
        provider_safety_tokens=1024,
    )
    history = (
        large_history()
        if not large_tail
        else [
            messages.ModelRequest(
                parts=[
                    messages.UserPromptPart(
                        "不能裁掉的最近内容" + "x" * 200_000
                    )
                ]
            )
        ]
    )
    try:
        saved = runtime.model_runtime.run(runtime.conversations.load())
        runtime.model_runtime.run(
            runtime.conversations.save(saved.summary, history)
        )
        response = runtime.agent.run("本轮必须原样保留")
        assert response.status is result.RunStatus.COMPLETED
        assert sum(not request["stream"] for request in scenario.requests) == (
            0 if large_tail else 1
        )
        wire = json.dumps(scenario.requests[-1], ensure_ascii=False)
        assert "本轮必须原样保留" in wire
        assert ("不能裁掉的最近内容" if large_tail else "历史问题19") in wire
        assert "过大的摘要" not in wire
    finally:
        runtime.close()


def test_provider_capacity_rejection_stops_without_retry_or_business_commit(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A { int n = 1; }")
    scenario = sdk_scenario([proposed_reply()])
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
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        original = authorize_task(runtime)
        response = runtime.agent.run("修复数值")
        assert response.status is result.RunStatus.FAILED
        assert len(scenario.requests) == 2
        assert runtime.repair.store.load() == original
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
    finally:
        runtime.close()


def test_summary_requests_share_native_fifty_request_budget(
    tmp_path, sdk_scenario
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="历史摘要"),
            *[
                sdk_fakes.Reply(
                    tool="read_memory",
                    arguments={"file": f"note-{index}.md"},
                    finish="tool_calls",
                )
                for index in range(51)
            ],
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    runtime.model_runtime.profile = dataclasses.replace(
        runtime.model_runtime.profile,
        context_window=50_000,
        max_output_tokens=1024,
        provider_safety_tokens=1024,
    )
    history = large_history()
    try:
        saved = runtime.model_runtime.run(runtime.conversations.load())
        runtime.model_runtime.run(
            runtime.conversations.save(saved.summary, history)
        )
        for index in range(51):
            runtime.model_runtime.run(
                runtime.memory.store.write(
                    memory.PREFIX + f"note-{index}.md",
                    f"独立内容-{index}",
                    expected_version=None,
                )
            )
        response = runtime.agent.run("依次读取全部不同笔记")
        assert response.status is result.RunStatus.LIMITED
        assert len(scenario.requests) == 50
        assert not scenario.requests[0]["stream"]
        assert (
            runtime.model_runtime.run(runtime.conversations.load()).messages
            == history
        )
    finally:
        runtime.close()


def test_native_loop_detection_nudges_instead_of_three_result_hard_stop(
    tmp_path, sdk_scenario
):
    (tmp_path / "A.java").write_text("class A {}")
    scenario = sdk_scenario(
        [
            *[
                sdk_fakes.Reply(
                    tool="read_source_file",
                    arguments={"path": "A.java"},
                    finish="tool_calls",
                )
                for _ in range(4)
            ],
            sdk_fakes.Reply(text="已读取证据，结束调查。"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        response = runtime.agent.run("读取 A.java 并说明")
        assert response.status is result.RunStatus.COMPLETED
        assert len(scenario.requests) == 5
    finally:
        runtime.close()


def test_ordinary_history_restores_after_restart_and_new_keeps_notebook(
    tmp_path, sdk_scenario
):
    first = cli_runtime.build_cli_runtime(
        tmp_path, sdk_scenario([sdk_fakes.Reply(text="第一轮回答")]).runtime
    )
    try:
        first.agent.run("第一轮问题")
        first.model_runtime.run(
            first.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "只接受人工审核",
                expected_version=None,
            )
        )
        old = first.model_runtime.run(first.conversations.load()).summary.id
    finally:
        first.close()
    scenario = sdk_scenario([sdk_fakes.Reply(text="第二轮回答")])
    second = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        second.agent.run("第二轮问题")
        wire = json.dumps(scenario.requests[0], ensure_ascii=False)
        assert "第一轮问题" in wire and "第一轮回答" in wire
        assert "只接受人工审核" in wire
        assert second.model_runtime.run(second.conversations.new()) != old
        assert not second.model_runtime.run(
            second.conversations.load()
        ).messages
        note = second.model_runtime.run(second.memory.show())
        assert note is not None
        assert note.content == "只接受人工审核"
    finally:
        second.close()


def test_answer_survives_conversation_cas_conflict(tmp_path, sdk_scenario):
    scenario = sdk_scenario([sdk_fakes.Reply(text="已生成回答")])
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    other = conversations.ProjectConversations(tmp_path)
    try:
        runtime.model_runtime.run(runtime.conversations.load())
        competing = runtime.model_runtime.run(other.load())

        async def competing_save():
            await other.save(
                competing.summary,
                [
                    messages.ModelRequest(
                        parts=[messages.UserPromptPart("另一进程")]
                    )
                ],
            )

        # 原模型请求等待期间，由另一存储实例抢先提交。
        scenario.before_response = competing_save
        notices = []
        response = runtime.agent.run(
            "生成", on_observation=lambda text, summary: notices.append(text)
        )
        assert response.final_answer == "已生成回答"
        assert any("会话未保存" in item for item in notices)
        saved = runtime.model_runtime.run(other.load())
        assert (
            "另一进程"
            in messages.ModelMessagesTypeAdapter.dump_json(
                saved.messages
            ).decode()
        )
    finally:
        runtime.close()


def test_notebook_is_native_cas_storage_and_export_contains_only_notes(
    tmp_path,
):
    notebook = memory.ProjectMemory(tmp_path)

    runner = model_runtime.ModelRuntime(
        None, context_window.resolve_context_profile(model="deepseek-flash")
    )
    try:
        runner.run(
            notebook.store.write(
                memory.PREFIX + "decision.md",
                "用户确认的决定",
                expected_version=None,
            )
        )
        item = runner.run(notebook.show("decision.md"))
        assert item is not None
        runner.run(
            notebook.store.write(
                memory.PREFIX + "decision.md",
                "新决定",
                expected_version=item.version,
            )
        )
        with pytest.raises(sdk_memory.MemoryConflictError):
            runner.run(
                notebook.store.write(
                    memory.PREFIX + "decision.md",
                    "过期覆盖",
                    expected_version=item.version,
                )
            )
        export = runner.run(notebook.export())
        assert json.loads(export.read_text(encoding="utf-8")) == {
            "decision.md": "新决定"
        }
        runner.run(notebook.forget("decision.md"))
        assert not runner.run(notebook.paths())
        with pytest.raises(ValueError, match="受保护"):
            runner.run(notebook.forget("MEMORY.md"))
    finally:
        runner.close()


def test_memory_storage_failure_degrades_but_contract_error_propagates(
    tmp_path, sdk_scenario, monkeypatch
):
    scenario = sdk_scenario([sdk_fakes.Reply(text="主任务仍有回答")])
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    notices = []

    async def fail(*args, **kwargs):
        raise sqlite3.OperationalError("storage unavailable")

    monkeypatch.setattr(runtime.memory.store, "read", fail)
    try:
        response = runtime.agent.run(
            "问答", on_observation=lambda text, summary: notices.append(text)
        )
        assert response.final_answer == "主任务仍有回答"
        assert any("存储不可用" in item for item in notices)

        async def contract_error(*args, **kwargs):
            raise TypeError("programming error")

        monkeypatch.setattr(runtime.memory.store, "read", contract_error)
        with pytest.raises(TypeError, match="programming error"):
            runtime.agent.run("再次问答")
    finally:
        runtime.close()


def test_notebook_is_project_local_and_clear_preserves_history_and_legacy(
    tmp_path, sdk_scenario
):
    state = tmp_path / ".autopatch-j"
    state.mkdir()
    legacy = state / "memory.db"
    legacy.write_bytes(b"untouched user data")
    projection = state / "memory_summary.md"
    projection.write_text("旧用户笔记", encoding="utf-8")
    runtime = cli_runtime.build_cli_runtime(
        tmp_path, sdk_scenario([sdk_fakes.Reply(text="普通回答")]).runtime
    )
    try:
        runtime.agent.run("历史应保留")
        runtime.model_runtime.run(
            runtime.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "只在本项目生效",
                expected_version=None,
            )
        )
        other = memory.ProjectMemory(tmp_path / "other-project")
        assert runtime.model_runtime.run(other.show()) is None
        runtime.model_runtime.run(runtime.memory.clear())
        assert runtime.model_runtime.run(runtime.memory.paths()) == []
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert len(saved.messages) == 2
        assert legacy.read_bytes() == b"untouched user data"
        assert projection.read_text(encoding="utf-8") == "旧用户笔记"
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
        cli_runtime.build_cli_runtime(tmp_path, lambda: resources)
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
