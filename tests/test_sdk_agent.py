"""以离线 HTTP 响应验证真实 SDK 的任务、工具和存储边界。"""

import asyncio
import dataclasses
import json
import sqlite3
from unittest import mock

from pydantic_ai import exceptions
from pydantic_ai import messages
from pydantic_ai_harness import memory as sdk_memory
import pytest

from autopatch_j.agent import classification
from autopatch_j.agent import result
from autopatch_j.cli import runtime as cli_runtime
from autopatch_j.core import conversations
from autopatch_j.core import memory
from autopatch_j.core.domain import intent as domain_intent
from autopatch_j.core.domain import workspace as domain_workspace
from autopatch_j.core.patching import types as patch_types
from autopatch_j.core.project import symbol_index
from autopatch_j.llm import context_window
from autopatch_j.llm import policy
from autopatch_j.llm import runtime as model_runtime
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
@pytest.mark.parametrize("tool", [False, True])
def test_abnormal_finish_cannot_execute_tool_or_save_success(
    tmp_path, sdk_scenario, finish, tool
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                text="不完整回答",
                tool="write_memory" if tool else None,
                arguments={"file": "decision.md", "content": "不应保存"},
                finish=finish,
            )
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        with pytest.raises(
            (
                policy.ModelFailure,
                exceptions.UnexpectedModelBehavior,
                exceptions.ModelAPIError,
            )
        ):
            runtime.agent.perform_general_chat("记录长期决定")
        assert not runtime.model_runtime.run(runtime.memory.paths())
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
        assert runtime.agent.session.proposed_patch_draft is None
        assert len(scenario.requests) == 1
    finally:
        runtime.close()


def test_sdk_tool_reads_source_and_preserves_reasoning_and_history(
    tmp_path, sdk_scenario
):
    source = tmp_path / "Demo.java"
    source.write_text("class Demo {}", encoding="utf-8")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_source_file",
                arguments={"path": "Demo.java"},
                finish="tool_calls",
                reasoning="先读取证据",
                fragment_arguments=True,
            ),
            sdk_fakes.Reply(text="Demo 是一个空类。"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    runtime.agent.session.set_focus_paths(["Demo.java"])
    try:
        response = runtime.agent.perform_code_explain(
            "解释此类", scope=None, allow_symbol_search=False
        )
        assert response.final_answer == "Demo 是一个空类。"
        assert [item.name for item in response.observations] == [
            "read_source_file"
        ]
        assert response.observations[0].status == "ok"
        tools = {
            item["function"]["name"] for item in scenario.requests[0]["tools"]
        }
        assert "search_symbols" not in tools
        assert {
            "read_memory",
            "search_memory",
            "write_memory",
            "delete_memory",
        } <= tools
        assert "max_tokens" in scenario.requests[0]
        assert "max_completion_tokens" not in scenario.requests[0]
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert any(
            isinstance(part, messages.ThinkingPart)
            for item in saved.messages
            if isinstance(item, messages.ModelResponse)
            for part in item.parts
        )
        assert source.read_text(encoding="utf-8") == "class Demo {}"
        assert not runtime.agent.session.source_read_cache
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "arguments",
    [
        {"path": "Demo.java", "line": True},
        {"path": "Demo.java", "line": "2"},
        {"path": "Demo.java", "line": 1.5},
        {"path": "Demo.java", "line": 1, "unknown": "not allowed"},
    ],
)
def test_invalid_tool_arguments_have_no_business_effect(
    tmp_path, sdk_scenario, arguments
):
    (tmp_path / "Demo.java").write_text("class Demo {}", encoding="utf-8")
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
    try:
        response = runtime.agent.perform_code_explain("解释", scope=None)
        assert response.observations == ()
        expected_error = (
            "unknown" if "unknown" in arguments else "valid integer"
        )
        assert any(
            item.get("role") == "tool"
            and expected_error in item.get("content", "")
            for item in scenario.requests[1]["messages"]
        )
    finally:
        runtime.close()


def test_ordinary_history_restores_after_restart_and_new_keeps_notebook(
    tmp_path, sdk_scenario
):
    first = cli_runtime.build_cli_runtime(
        tmp_path, sdk_scenario([sdk_fakes.Reply(text="第一轮回答")]).runtime
    )
    try:
        first.agent.perform_general_chat("第一轮问题")
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
        second.agent.perform_general_chat("第二轮问题")
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


def test_repair_does_not_import_or_save_ordinary_history(
    tmp_path, sdk_scenario
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="普通回答"),
            sdk_fakes.Reply(text="未发现明确问题"),
            sdk_fakes.Reply(text="仍未发现明确问题"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        runtime.agent.perform_general_chat("普通会话专属信息")
        runtime.agent.perform_zero_finding_review("检查", "Demo.java")
        runtime.agent.perform_zero_finding_review("再次检查", "Other.java")
        assert "普通会话专属信息" not in json.dumps(
            scenario.requests[1], ensure_ascii=False
        )
        assert "Demo.java" not in json.dumps(
            scenario.requests[2], ensure_ascii=False
        )
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert len(saved.messages) == 2
    finally:
        runtime.close()


def test_failed_round_preserves_completed_history(tmp_path, sdk_scenario):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="成功回答"),
            sdk_fakes.Reply(text="半截回答", finish="length"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        runtime.agent.perform_general_chat("成功请求")
        with pytest.raises(policy.ModelFailure):
            runtime.agent.perform_general_chat("失败请求")
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert (
            "失败请求"
            not in messages.ModelMessagesTypeAdapter.dump_json(
                saved.messages
            ).decode()
        )
        assert len(saved.messages) == 2
    finally:
        runtime.close()


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
        response = runtime.agent.perform_general_chat(
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


def test_classifier_is_one_short_nonstream_call_and_rejects_abnormal(
    tmp_path, sdk_scenario
):
    del tmp_path
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="code_audit"),
            sdk_fakes.Reply(text="patch_revise", finish="length"),
        ]
    )
    runtime = scenario.runtime()
    try:
        classifier = classification.build_intent_classifier(runtime)
        assert classifier("检查代码", False) == (
            domain_intent.IntentType.CODE_AUDIT,
            "",
        )
        intent, error = classifier("重写", True)
        assert intent is None and error
        assert len(scenario.requests) == 2
        assert all(
            not item["stream"]
            and item["max_tokens"] == 128
            and item["temperature"] == 0
            and item["thinking"] == {"type": "disabled"}
            for item in scenario.requests
        )
    finally:
        runtime.close()


def test_model_request_limit_stops_at_ten(tmp_path, sdk_scenario):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_memory",
                arguments={"file": f"note-{index}.md"},
                finish="tool_calls",
            )
            for index in range(11)
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    for index in range(11):
        runtime.model_runtime.run(
            runtime.memory.store.write(
                memory.PREFIX + f"note-{index}.md",
                f"唯一笔记内容 {index}",
                expected_version=None,
            )
        )
    try:
        response = runtime.agent.perform_general_chat("查找笔记")
        assert response.status is result.RunStatus.LIMITED
        assert len(scenario.requests) <= 10
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages
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


@pytest.mark.parametrize("end", ["stop", "length", "cancel"])
def test_proposed_draft_is_committed_only_after_eligible_end(
    tmp_path, sdk_scenario, end
):
    (tmp_path / "Demo.java").write_text("class Demo {}", encoding="utf-8")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="propose_patch",
                arguments={
                    "file_path": "Demo.java",
                    "old_string": "class Demo",
                    "new_string": "class Renamed",
                    "rationale": "已确认修复",
                },
                finish="tool_calls",
            ),
            sdk_fakes.Reply(
                text="完成", finish="stop" if end == "cancel" else end
            ),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    runtime.agent.session.set_focus_paths(["Demo.java"])
    if end == "cancel":

        def cancel_after_tool():
            if len(scenario.requests) == 2:
                runtime.model_runtime.cancel()

        scenario.before_reply = cancel_after_tool
    try:
        if end == "length":
            with pytest.raises(policy.ModelFailure):
                runtime.agent.perform_zero_finding_review("改名", "Demo.java")
        else:
            response = runtime.agent.perform_zero_finding_review(
                "改名", "Demo.java"
            )
            assert response.status is (
                result.RunStatus.CANCELLED
                if end == "cancel"
                else result.RunStatus.COMPLETED
            )
        assert (runtime.agent.session.proposed_patch_draft is not None) == (
            end == "stop"
        )
        assert not runtime.workspace_manager.load().patch_items
        assert (tmp_path / "Demo.java").read_text(
            encoding="utf-8"
        ) == "class Demo {}"
        if end == "stop":
            request = json.dumps(scenario.requests[1], ensure_ascii=False)
            assert "暂存草案绑定" in request and "Demo.java" in request
    finally:
        runtime.close()
    assert runtime.model_runtime.closed
    assert runtime.model_runtime.client is not None
    assert runtime.model_runtime.client.is_closed()
    runtime.close()


@pytest.mark.parametrize("summary_finish", ["stop", "length"])
def test_compaction_keeps_current_user_and_rejects_invalid_summary(
    tmp_path, sdk_scenario, summary_finish
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                text=(
                    "不可采纳的摘要"
                    if summary_finish != "stop"
                    else "有效历史摘要"
                ),
                finish=summary_finish,
            ),
            sdk_fakes.Reply(text="最终回答"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    history = _large_history()
    try:
        saved = runtime.model_runtime.run(runtime.conversations.load())
        runtime.model_runtime.run(
            runtime.conversations.save(saved.summary, history)
        )
        response = runtime.agent.perform_general_chat(
            "本轮原文和必须保留的约束"
        )
        assert response.final_answer == "最终回答"
        assert len(scenario.requests) == 2
        assert scenario.requests[0]["stream"] is False
        wire = json.dumps(scenario.requests[1], ensure_ascii=False)
        assert "本轮原文和必须保留的约束" in wire
        assert "不可采纳的摘要" not in wire
        if summary_finish == "stop":
            assert "有效历史摘要" in wire
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert len(saved.messages) < len(history)
    finally:
        runtime.close()


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
        response = runtime.agent.perform_general_chat(
            "问答", on_observation=lambda text, summary: notices.append(text)
        )
        assert response.final_answer == "主任务仍有回答"
        assert any("存储不可用" in item for item in notices)

        async def contract_error(*args, **kwargs):
            raise TypeError("programming error")

        monkeypatch.setattr(runtime.memory.store, "read", contract_error)
        with pytest.raises(TypeError, match="programming error"):
            runtime.agent.perform_general_chat("再次问答")
    finally:
        runtime.close()


def test_applied_state_survives_rescan_failure_and_rejects_old_review(
    tmp_path, sdk_scenario, monkeypatch
):
    source = tmp_path / "Demo.java"
    source.write_text("class Demo {}", encoding="utf-8")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="propose_patch",
                arguments={
                    "file_path": "Demo.java",
                    "old_string": "class Demo",
                    "new_string": "class Renamed",
                    "rationale": "用户要求改名",
                },
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="请审核"),
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    runtime.agent.session.set_focus_paths(["Demo.java"])
    try:
        runtime.agent.perform_zero_finding_review(
            "将 Demo 改名 Renamed", "Demo.java"
        )
        draft = runtime.agent.session.pop_proposed_patch_draft()
        assert draft is not None
        runtime.workspace_manager.add_patch(draft)
        rejected = runtime.review_service.apply(
            dataclasses.replace(draft, new_string="class Unreviewed")
        )
        assert rejected.application.error_code == "REVIEW_BINDING_CHANGED"
        assert source.read_text(encoding="utf-8") == "class Demo {}"
        verifier = mock.MagicMock()
        verifier.verify_finding_resolved.side_effect = RuntimeError(
            "scan unavailable"
        )
        monkeypatch.setattr(runtime, "patch_verifier", verifier)
        outcome = runtime.review_service.apply(draft)
        assert outcome.application.applied
        assert outcome.verification is not None
        assert (
            outcome.verification.outcome
            is patch_types.VerificationOutcome.UNVERIFIED
        )
        assert "scan unavailable" in outcome.verification.message
        saved = runtime.workspace_manager.load()
        assert (
            saved.patch_items[0].status
            is domain_workspace.PatchReviewStatus.APPLIED
        )
        assert saved.current_patch() is None
        assert source.read_text(encoding="utf-8") == "class Renamed {}"
        assert not runtime.review_service.apply(draft).application.applied
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
        runtime.agent.perform_general_chat("历史应保留")
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


def _large_history() -> list[messages.ModelMessage]:
    history: list[messages.ModelMessage] = []
    for index in range(20):
        history.extend(
            [
                messages.ModelRequest(
                    parts=[
                        messages.UserPromptPart(
                            f"历史问题 {index} " + "x" * 25_000
                        )
                    ]
                ),
                messages.ModelResponse(
                    parts=[
                        messages.TextPart(f"历史回答 {index} " + "y" * 25_000)
                    ]
                ),
            ]
        )
    return history


def test_summary_and_tools_share_one_request_limit(tmp_path, sdk_scenario):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(text="压缩后的历史"),
            *[
                sdk_fakes.Reply(
                    tool="read_memory",
                    arguments={"file": f"note-{index}.md"},
                    finish="tool_calls",
                )
                for index in range(10)
            ],
        ]
    )
    runtime = cli_runtime.build_cli_runtime(tmp_path, scenario.runtime)
    try:
        saved = runtime.model_runtime.run(runtime.conversations.load())
        runtime.model_runtime.run(
            runtime.conversations.save(saved.summary, _large_history())
        )
        for index in range(10):
            runtime.model_runtime.run(
                runtime.memory.store.write(
                    memory.PREFIX + f"note-{index}.md",
                    f"不同内容 {index}",
                    expected_version=None,
                )
            )
        response = runtime.agent.perform_general_chat("查阅所有不同笔记")
        assert response.status is result.RunStatus.LIMITED
        assert len(scenario.requests) == 10
        assert scenario.requests[0]["stream"] is False
        assert all(item["stream"] for item in scenario.requests[1:])
        persisted = runtime.model_runtime.run(runtime.conversations.load())
        assert len(persisted.messages) == 40
    finally:
        runtime.close()
