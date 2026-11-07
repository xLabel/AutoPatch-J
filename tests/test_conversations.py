"""原生会话持久化、重启、并发与取消的项目契约。"""

import asyncio
from concurrent import futures
import json
import threading

import filelock
from pydantic_ai import messages
from pydantic_ai_harness.step_persistence import (
    conversations as sdk_conversations,
)
import pytest

from autopatch_j import config
from autopatch_j.agent import result
from autopatch_j.application import runtime as project_runtime
from autopatch_j.core import conversations
from autopatch_j.core import memory
from autopatch_j.core.repair import models
from tests import sdk_fakes


def test_ordinary_history_restores_after_restart_and_new_keeps_notebook(
    tmp_path, sdk_scenario
):
    first = project_runtime.build_project_runtime(
        tmp_path,
        config.AppConfig(),
        sdk_scenario([sdk_fakes.Reply(text="第一轮回答")]).runtime,
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
    second = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
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
    runtime = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
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
        with pytest.raises(models.StateConflict) as caught:
            runtime.agent.run("生成")
        assert "已生成回答" in caught.value.__notes__
        saved = runtime.model_runtime.run(other.load())
        assert (
            "另一进程"
            in messages.ModelMessagesTypeAdapter.dump_json(
                saved.messages
            ).decode()
        )
    finally:
        runtime.close()


@pytest.mark.parametrize("limit", [1, 3, None])
def test_native_limit_preserves_evidence_and_restart_continues(
    tmp_path, sdk_scenario, limit
):
    for index in range(5):
        (tmp_path / f"A{index}.java").write_text(
            f'class A{index} {{ String value = "EVIDENCE_{index}"; }}'
        )
    scenario = sdk_scenario(
        [
            *[
                sdk_fakes.Reply(
                    tool="read_source_file",
                    arguments={"path": f"A{i}.java"},
                    finish="tool_calls",
                )
                for i in range(5)
            ],
            sdk_fakes.Reply(text="读取完成"),
        ]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(llm_request_limit=limit), scenario.runtime
    )
    try:
        # 先初始化复用的 Runner，捕获不能依赖同步调用方的 ContextVar。
        project.model_runtime.run(project.conversations.load())
        response = project.agent.run("调查这些 Java 文件，不修改源码")
        saved = project.model_runtime.run(project.conversations.load())
        assert saved.messages
        assert (
            "EVIDENCE_0"
            in messages.ModelMessagesTypeAdapter.dump_json(
                saved.messages
            ).decode()
        )
        if limit is None:
            assert response.status is result.RunStatus.COMPLETED
            assert len(scenario.requests) == 6
        else:
            assert response.status is result.RunStatus.LIMITED
            assert len(scenario.requests) == limit
            assert "已保存" in response.final_answer
            assert saved.summary.outcome == "ready"
    finally:
        project.close()
    if limit is not None:
        resumed = sdk_scenario([sdk_fakes.Reply(text="继续调查")])
        restarted = project_runtime.build_project_runtime(
            tmp_path, config.AppConfig(llm_request_limit=limit), resumed.runtime
        )
        try:
            assert (
                restarted.agent.run("继续").status is result.RunStatus.COMPLETED
            )
            assert len(resumed.requests) == 1
            assert "EVIDENCE_0" in json.dumps(
                resumed.requests[0], ensure_ascii=False
            )
        finally:
            restarted.close()
    assert "EVIDENCE_0" in (tmp_path / "A0.java").read_text()


def test_withdrawn_requirement_is_not_reinjected_after_restart(
    tmp_path, sdk_scenario
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="update_task_requirements",
                finish="tool_calls",
                arguments={"additions": ["保留日志格式"], "removals": []},
            ),
            sdk_fakes.Reply(text="已记录"),
            sdk_fakes.Reply(
                tool="update_task_requirements",
                finish="tool_calls",
                arguments={
                    "additions": ["日志改为 JSON"],
                    "removals": ["保留日志格式"],
                },
            ),
            sdk_fakes.Reply(text="已修订"),
        ]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    try:
        initial = project.repair.store.load()
        project.repair.commit(
            initial, project.repair.start(initial, "修复日志", ())
        )
        assert (
            project.agent.run("保留日志格式").status
            is result.RunStatus.COMPLETED
        )
        assert (
            project.agent.run("撤回上一条，日志改为 JSON").status
            is result.RunStatus.COMPLETED
        )
        assert project.repair.store.load().constraints == ("日志改为 JSON",)
    finally:
        project.close()
    resumed = sdk_scenario([sdk_fakes.Reply(text="继续")])
    restarted = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), resumed.runtime
    )
    try:
        restarted.agent.run("继续")
        binding_text = next(
            item["content"].split(
                "当前用户与业务绑定（不代表源码已应用）：", 1
            )[1]
            for item in resumed.requests[0]["messages"]
            if item["role"] == "system"
            and "temporary_requirements" in item["content"]
        )
        assert json.loads(binding_text)["temporary_requirements"] == [
            "日志改为 JSON"
        ]
        names = {
            item["function"]["name"] for item in resumed.requests[0]["tools"]
        }
        assert (
            "update_task_requirements" in names
            and "record_constraint" not in names
        )
    finally:
        restarted.close()


def test_limited_save_failure_reports_lost_history(
    tmp_path, sdk_scenario, monkeypatch
):
    (tmp_path / "A.java").write_text("class A {}")
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_source_file",
                arguments={"path": "A.java"},
                finish="tool_calls",
            )
        ]
    )
    project = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(llm_request_limit=1), scenario.runtime
    )

    async def fail_save(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(project.conversations, "save", fail_save)
    try:
        with pytest.raises(OSError, match="disk full") as caught:
            project.agent.run("调查 A.java")
        assert any("会话未确认保存" in note for note in caught.value.__notes__)
        assert not project.model_runtime.run(
            project.conversations.load()
        ).messages
    finally:
        project.close()


def test_existing_runtime_follows_new_session_without_resurrecting_old(
    tmp_path, sdk_scenario
):
    scenario = sdk_scenario(
        [sdk_fakes.Reply(text="旧回答"), sdk_fakes.Reply(text="新回答")]
    )
    first = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), scenario.runtime
    )
    other = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), sdk_scenario([]).runtime
    )
    try:
        first.agent.run("OLD_SESSION_ONLY")
        old = first.model_runtime.run(first.conversations.load())
        new_id = other.new_conversation(other.repair.store.load())
        assert first.agent.run("新问题").status is result.RunStatus.COMPLETED
        assert "OLD_SESSION_ONLY" not in json.dumps(scenario.requests[-1])
        active = first.model_runtime.run(first.conversations.load())
        assert active.summary.id == new_id
        archived = first.model_runtime.run(
            first.conversations.store.get(conversation_id=old.summary.id)
        )
        assert archived.summary.archived_at is not None
        with pytest.raises(sdk_conversations.ConversationConflict):
            first.model_runtime.run(
                first.conversations.save(old.summary, old.messages)
            )
    finally:
        first.close()
        other.close()


@pytest.mark.parametrize("new_first", [True, False])
def test_new_and_readonly_final_save_share_project_lock(
    tmp_path, sdk_scenario, monkeypatch, new_first
):
    entered = threading.Event()
    release = threading.Event()
    scenario = sdk_scenario([sdk_fakes.Reply(text="已回答")])
    other = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), sdk_scenario([]).runtime
    )
    new_id = None

    async def pause_model():
        entered.set()
        assert await asyncio.to_thread(release.wait, 10)

    def run_first():
        project = project_runtime.build_project_runtime(
            tmp_path, config.AppConfig(), scenario.runtime
        )
        if new_first:
            scenario.before_response = pause_model
        else:
            real_save = project.conversations.save

            async def pause_save(summary, history):
                entered.set()
                assert await asyncio.to_thread(release.wait, 10)
                await real_save(summary, history)

            monkeypatch.setattr(project.conversations, "save", pause_save)
        try:
            return project.agent.run("只读调查，不开始修复任务")
        finally:
            project.close()

    response = None
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_first)
            try:
                assert entered.wait(10)
                old = other.model_runtime.run(other.conversations.load())
                if new_first:
                    new_id = other.new_conversation(other.repair.store.load())
                else:
                    # 确认保存实际持有业务锁，不用 sleep 猜测线程是否已经阻塞。
                    with pytest.raises(filelock.Timeout):
                        with other.repair.store.lock.acquire(timeout=0):
                            pass
            finally:
                release.set()
            if new_first:
                with pytest.raises(models.StateConflict):
                    future.result(timeout=10)
            else:
                response = future.result(timeout=10)
        if not new_first:
            assert response is not None
            assert response.status is result.RunStatus.COMPLETED
            new_id = other.new_conversation(other.repair.store.load())
        active = other.model_runtime.run(other.conversations.load())
        assert new_id is not None
        assert active.summary.id == new_id and not active.messages
        archived = other.model_runtime.run(
            other.conversations.store.get(conversation_id=old.summary.id)
        )
        assert archived.summary.archived_at is not None
    finally:
        release.set()
        other.close()


@pytest.mark.parametrize("during_load", [True, False])
def test_cancel_waits_for_native_storage_before_releasing_lock(
    tmp_path, sdk_scenario, monkeypatch, during_load
):
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    holders = []
    scenario = sdk_scenario([sdk_fakes.Reply(text="已回答")])
    other = project_runtime.build_project_runtime(
        tmp_path, config.AppConfig(), sdk_scenario([]).runtime
    )

    def run_first():
        project = project_runtime.build_project_runtime(
            tmp_path, config.AppConfig(), scenario.runtime
        )
        holders.append(project)
        # 阻塞真实 SDK 后台写入；公开 save 包装无法复现取消后线程仍在写的边界。
        # pylint: disable-next=protected-access
        real_save = project.conversations.store._save

        def delayed_save(expected, summary, payload, text):
            if (summary.message_count == 0) == during_load:
                entered.set()
                assert release.wait(10)
            return real_save(expected, summary, payload, text)

        monkeypatch.setattr(project.conversations.store, "_save", delayed_save)
        try:
            return project.agent.run("只读调查")
        finally:
            project.close()

    response = None
    try:
        with futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_first)
            try:
                assert entered.wait(10)
                project = holders[0]
                loop = project.model_runtime.runner.get_loop()

                def cancel_and_signal():
                    project.model_runtime.cancel()
                    loop.call_soon(cancelled.set)

                loop.call_soon_threadsafe(cancel_and_signal)
                assert cancelled.wait(10)
                with pytest.raises(filelock.Timeout):
                    with other.repair.store.lock.acquire(timeout=0):
                        pass
            finally:
                release.set()
            response = future.result(timeout=10)
        saved = other.model_runtime.run(other.conversations.load())
        if during_load:
            assert response.status is result.RunStatus.CANCELLED
            assert not scenario.requests and not saved.messages
        else:
            assert response.status is result.RunStatus.COMPLETED
            assert saved.summary.outcome == "completed"
            assert (
                "已回答"
                in messages.ModelMessagesTypeAdapter.dump_json(
                    saved.messages
                ).decode()
            )
        other.new_conversation(other.repair.store.load())
        assert not other.model_runtime.run(other.conversations.load()).messages
    finally:
        release.set()
        other.close()
