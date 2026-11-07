"""项目笔记管理、隔离与存储故障传播。"""

import asyncio
import json
import sqlite3

from pydantic_ai_harness import memory as sdk_memory
import pytest

from autopatch_j import config as autopatch_j_config
from autopatch_j.application import runtime as project_runtime
from autopatch_j.core import memory
from tests import sdk_fakes


def test_clear_failure_keeps_actual_partial_deletion(tmp_path, monkeypatch):
    notebook = memory.ProjectMemory(tmp_path)
    delete = notebook.store.delete
    deleted = []

    async def fail_after_one(path, **kwargs):
        if deleted:
            raise sqlite3.OperationalError("storage locked")
        await delete(path, **kwargs)
        deleted.append(path)

    with asyncio.Runner() as runner:
        for name in ("MEMORY.md", "topic.md"):
            runner.run(
                notebook.store.write(
                    "project/main/" + name, name, expected_version=None
                )
            )
        monkeypatch.setattr(notebook.store, "delete", fail_after_one)
        with pytest.raises(sqlite3.OperationalError) as caught:
            runner.run(notebook.clear())
        remaining = runner.run(notebook.paths())
        assert len(deleted) == 1 and len(remaining) == 1
        assert deleted[0].removeprefix("project/main/") not in remaining
        assert "已删除的笔记未恢复" in " ".join(caught.value.__notes__)


def test_export_failure_identifies_partial_file(tmp_path, monkeypatch):
    notebook = memory.ProjectMemory(tmp_path)

    def incomplete_dump(unused_content, output, **unused_options):
        output.write("{")
        raise OSError("disk full")

    monkeypatch.setattr(memory.json, "dump", incomplete_dump)
    with asyncio.Runner() as runner:
        with pytest.raises(OSError) as caught:
            runner.run(notebook.export())
    exports = list(notebook.state_dir.glob("memory-export-*.json"))
    assert len(exports) == 1
    assert exports[0].read_text() == "{"
    assert "导出文件可能不完整" in " ".join(caught.value.__notes__)
    assert exports[0].name in " ".join(caught.value.__notes__)


def test_notebook_is_native_cas_storage_and_export_contains_only_notes(
    tmp_path,
):
    notebook = memory.ProjectMemory(tmp_path)

    runner = asyncio.Runner()
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
        with pytest.raises(ValueError, match="不能单独删除"):
            runner.run(notebook.forget("MEMORY.md"))
    finally:
        runner.close()


def test_memory_injection_failure_cannot_reuse_old_snapshot(
    tmp_path, sdk_scenario, monkeypatch
):
    scenario = sdk_scenario([sdk_fakes.Reply(text="首轮回答")])
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    try:
        runtime.model_runtime.run(
            runtime.memory.store.write(
                memory.PREFIX + "MEMORY.md",
                "OLD_MEMORY_SNAPSHOT",
                expected_version=None,
            )
        )
        runtime.agent.run("首轮读取记忆")
        saved = runtime.model_runtime.run(runtime.conversations.load())
        assert "OLD_MEMORY_SNAPSHOT" in json.dumps(scenario.requests[0])
        runtime.model_runtime.run(runtime.memory.clear())
        assert not runtime.model_runtime.run(runtime.memory.paths())

        async def fail(*unused_args, **unused_kwargs):
            raise sqlite3.OperationalError("storage unavailable")

        monkeypatch.setattr(runtime.memory.store, "read", fail)
        with pytest.raises(
            sqlite3.OperationalError, match="storage unavailable"
        ):
            runtime.agent.run("不能依靠旧快照继续")
        assert len(scenario.requests) == 1
        assert not runtime.model_runtime.run(runtime.memory.paths())
        assert (
            runtime.model_runtime.run(runtime.conversations.load()).messages
            == saved.messages
        )
    finally:
        runtime.close()


@pytest.mark.parametrize(
    "error_type", [sqlite3.OperationalError, OSError, TypeError]
)
def test_memory_tool_faults_stop_without_followup(
    tmp_path, sdk_scenario, monkeypatch, error_type
):
    scenario = sdk_scenario(
        [
            sdk_fakes.Reply(
                tool="read_memory",
                arguments={"file": "topic.md"},
                finish="tool_calls",
            ),
            sdk_fakes.Reply(text="已说明存储故障"),
        ]
    )
    runtime = project_runtime.build_project_runtime(
        tmp_path, autopatch_j_config.AppConfig(), scenario.runtime
    )
    original_read = runtime.memory.store.read

    async def read(path, **kwargs):
        if path.endswith("topic.md"):
            raise error_type("injected failure")
        return await original_read(path, **kwargs)

    monkeypatch.setattr(runtime.memory.store, "read", read)
    try:
        with pytest.raises(error_type, match="injected failure"):
            runtime.agent.run("读取主题")
        assert len(scenario.requests) == 1
        assert not runtime.model_runtime.run(
            runtime.conversations.load()
        ).messages

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
    runtime = project_runtime.build_project_runtime(
        tmp_path,
        autopatch_j_config.AppConfig(),
        sdk_scenario([sdk_fakes.Reply(text="普通回答")]).runtime,
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
