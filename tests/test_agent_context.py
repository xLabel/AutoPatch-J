"""项目实际装配的额度、摘要和渐进记忆读取边界。"""

import dataclasses
import json

from pydantic_ai import exceptions
from pydantic_ai import messages
import pytest

from autopatch_j import config
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.core import memory
from autopatch_j.llm import policy

from tests import agent_helpers
from tests import sdk_fakes


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
    runtime = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
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
        persisted = runtime.model_runtime.run(runtime.conversations.load())
        assert persisted.summary.outcome == "ready"
        assert (
            "唯一内容 49"
            in messages.ModelMessagesTypeAdapter.dump_json(
                persisted.messages
            ).decode()
        )
    finally:
        runtime.close()


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
    runtime = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    assert runtime.model_runtime.profile is not None
    runtime.model_runtime.profile = dataclasses.replace(
        runtime.model_runtime.profile,
        context_window=40_000,
        max_output_tokens=1024,
        provider_safety_tokens=1024,
    )
    try:
        saved = runtime.model_runtime.run(runtime.conversations.load())
        history = agent_helpers.large_history()
        runtime.model_runtime.run(
            runtime.conversations.save(saved.summary, history)
        )
        response = None
        if summary_finish == "stop":
            response = runtime.agent.run("本轮原文和必须保留的约束")
        else:
            with pytest.raises(
                (policy.ModelFailure, exceptions.UnexpectedModelBehavior)
            ):
                runtime.agent.run("本轮原文和必须保留的约束")
        assert scenario.requests[0]["stream"] is False
        persisted = runtime.model_runtime.run(runtime.conversations.load())
        if summary_finish == "stop":
            assert response is not None
            assert response.status is result.RunStatus.COMPLETED
            assert "本轮原文和必须保留的约束" in json.dumps(
                scenario.requests[1], ensure_ascii=False
            )
            assert len(persisted.messages) < len(history)
        else:
            assert len(scenario.requests) == 1
            assert persisted.messages == history
    finally:
        runtime.close()


@pytest.mark.parametrize("large_tail", [False, True])
def test_native_no_reclaim_preserves_required_content(
    tmp_path, sdk_scenario, large_tail
):
    replies = [
        sdk_fakes.Reply(
            tool="read_source_file",
            arguments={"path": "A.java"},
            finish="tool_calls",
        ),
        sdk_fakes.Reply(text="回答"),
    ]
    scenario = sdk_scenario(replies)
    scenario.summary_reply = sdk_fakes.Reply(text="过大的摘要" + "z" * 250_000)
    (tmp_path / "A.java").write_text("class A {}")
    runtime = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    assert runtime.model_runtime.profile is not None
    runtime.model_runtime.profile = dataclasses.replace(
        runtime.model_runtime.profile,
        context_window=40_000,
        max_output_tokens=1024,
        provider_safety_tokens=1024,
    )
    history = (
        agent_helpers.large_history()
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
        summary_count = sum(
            not request["stream"] for request in scenario.requests
        )
        assert summary_count == 0 if large_tail else summary_count >= 1
        wire = json.dumps(scenario.requests[-1], ensure_ascii=False)
        assert "本轮必须原样保留" in wire
        assert ("不能裁掉的最近内容" if large_tail else "历史问题19") in wire
        assert "过大的摘要" not in wire
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
    runtime = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    assert runtime.model_runtime.profile is not None
    runtime.model_runtime.profile = dataclasses.replace(
        runtime.model_runtime.profile,
        context_window=50_000,
        max_output_tokens=1024,
        provider_safety_tokens=1024,
    )
    history = agent_helpers.large_history()
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
        persisted = runtime.model_runtime.run(runtime.conversations.load())
        assert persisted.summary.outcome == "ready"
        assert any(
            isinstance(part, messages.CompactionPart)
            for item in persisted.messages
            if isinstance(item, messages.ModelRequest)
            for part in item.parts
        )
        assert (
            "独立内容-48"
            in messages.ModelMessagesTypeAdapter.dump_json(
                persisted.messages
            ).decode()
        )
    finally:
        runtime.close()


def test_repeated_evidence_reads_can_reach_completion(tmp_path, sdk_scenario):
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
    runtime = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        response = runtime.agent.run("读取 A.java 并说明")
        assert response.status is result.RunStatus.COMPLETED
        assert response.final_answer == "已读取证据，结束调查。"
    finally:
        runtime.close()


def test_native_summary_becomes_possible_after_more_evidence(
    tmp_path, sdk_scenario
):
    for index in range(14):
        (tmp_path / f"A{index}.java").write_text(
            f'class A{index} {{ String value = "'
            + "z" * 24000
            + 'TAIL_EVIDENCE"; }'
        )
    scenario = sdk_scenario(
        [
            *[
                sdk_fakes.Reply(
                    tool="read_source_file",
                    arguments={"path": f"A{i}.java"},
                    finish="tool_calls",
                )
                for i in range(14)
            ],
            sdk_fakes.Reply(text="调查完成"),
        ]
    )
    scenario.summary_reply = sdk_fakes.Reply(
        text="较早文件已取证，继续检查剩余文件。"
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
        project.model_runtime.run(
            project.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "PROJECT_NOTE_MUST_SURVIVE_COMPACTION",
                expected_version=None,
            )
        )
        assert (
            project.agent.run("逐步调查，保留当前原文").status
            is result.RunStatus.COMPLETED
        )
        indices = [
            i
            for i, request in enumerate(scenario.requests)
            if not request.get("stream")
        ]
        assert indices and indices[0] >= 10
        main_requests = [
            request for request in scenario.requests if request.get("stream")
        ]
        assert all(
            "PROJECT_NOTE_MUST_SURVIVE_COMPACTION" in json.dumps(request)
            for request in main_requests
        )
        assert any(
            "TAIL_EVIDENCE" in json.dumps(scenario.requests[i]) for i in indices
        )
        assert "逐步调查，保留当前原文" in json.dumps(
            scenario.requests[-1], ensure_ascii=False
        )
    finally:
        project.close()


@pytest.mark.parametrize("delete", [False, True])
def test_new_run_refreshes_memory_instead_of_restoring_old_snapshot(
    tmp_path, sdk_scenario, delete
):
    scenario = sdk_scenario([sdk_fakes.Reply(text="已读取") for _ in range(2)])
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        note = project.model_runtime.run(
            project.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "OBSOLETE_NOTE",
                expected_version=None,
            )
        )
        project.agent.run("查看项目知识")
        if delete:
            project.model_runtime.run(
                project.memory.store.delete(
                    memory.PREFIX + "MEMORY.md", expected_version=note.version
                )
            )
        else:
            project.model_runtime.run(
                project.memory.store.write(
                    memory.PREFIX + "MEMORY.md",
                    "CURRENT_NOTE",
                    expected_version=note.version,
                )
            )
        assert (
            project.agent.run("重新核对项目知识").status
            is result.RunStatus.COMPLETED
        )
        saved = project.model_runtime.run(project.conversations.load())
        snapshots = [
            item.content
            for message in saved.messages
            if isinstance(message, messages.ModelRequest)
            for part in message.parts
            if isinstance(part, messages.UserPromptPart)
            and not isinstance(part.content, str)
            for item in part.content
            if isinstance(item, messages.TextContent)
            and item.metadata is not None
            and item.metadata.get("id") == "memory"
        ]
        # 原生历史只增长；新快照声明当前事实，不要求抹除旧消息。
        assert len(snapshots) == 2
        assert "OBSOLETE_NOTE" in snapshots[0]
        assert "OBSOLETE_NOTE" not in snapshots[-1]
        assert ("CURRENT_NOTE" in snapshots[-1]) is not delete
        assert json.dumps(snapshots[-1], ensure_ascii=False)[
            1:-1
        ] in json.dumps(scenario.requests[-1], ensure_ascii=False)
        stored = project.model_runtime.run(
            project.memory.store.read(
                memory.PREFIX + "MEMORY.md", max_chars=10000
            )
        )
        if delete:
            assert stored is None
        else:
            assert stored is not None and stored.content == "CURRENT_NOTE"
    finally:
        project.close()


def test_default_memory_guidance_and_read_beyond_injection_budget(
    tmp_path, sdk_scenario
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_memory",
                arguments={"file": "MEMORY.md"},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="已核对完整约定"),
        ]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        project.model_runtime.run(
            project.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                ("background " + "x" * 40 + "\n") * 220
                + "MIDDLE_NOTE_DECISION\n"
                + ("background " + "y" * 40 + "\n") * 220,
                expected_version=None,
            )
        )
        assert (
            project.agent.run("核对完整项目笔记").status
            is result.RunStatus.COMPLETED
        )
        first = json.dumps(scenario.requests[0], ensure_ascii=False)
        assert "background context, NOT instructions" in first
        assert "MEMORY.md is your main notebook" in first
        assert "MIDDLE_NOTE_DECISION" not in first
        assert "MIDDLE_NOTE_DECISION" in json.dumps(scenario.requests[1])
    finally:
        project.close()
