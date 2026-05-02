from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from autopatch_j.llm.client import LLMCallPurpose
from autopatch_j.core.models import IntentType
from autopatch_j.core.memory import (
    MAX_RECENT_TURNS,
    MemorySummaryTrigger,
    MemoryManager,
)
from autopatch_j.core.memory.scheduler import MemorySummaryScheduler
from autopatch_j.core.memory.summarizer import MemorySummarizer


def _manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(tmp_path / ".autopatch-j" / "memory.json")


def test_missing_memory_file_loads_empty_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    memory = manager.load()

    assert memory["version"] == 1
    assert memory["working_memory"]["recent_turns"] == []
    assert memory["long_term_memory"]["durable_preferences"] == []


def test_append_recent_turn_writes_project_memory_file(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    manager.append_recent_turn(
        intent=IntentType.CODE_EXPLAIN,
        user_text="这个项目是干什么的",
        assistant_text="这是一个 Java CLI 项目。",
        scope_paths=["src/main/java/demo/App.java"],
    )

    memory = json.loads(manager.memory_file.read_text(encoding="utf-8"))
    turn = memory["working_memory"]["recent_turns"][0]
    assert turn["intent"] == "code_explain"
    assert turn["summary_status"] == "pending"
    assert turn["summary"] == ""
    assert turn["scope_paths"] == ["src/main/java/demo/App.java"]


def test_prompt_context_uses_pending_user_text_but_not_assistant_text(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="assistant answer should stay out of prompt",
    )

    context = manager.build_prompt_context(IntentType.CODE_EXPLAIN, "继续讲项目代码")

    assert "Optional 怎么用" in context
    assert "assistant answer should stay out of prompt" not in context


def test_prompt_context_includes_ready_summaries(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="answer",
    )
    memory = manager.load()
    turn_id = memory["working_memory"]["recent_turns"][0]["id"]

    manager.apply_delta(
        {
            "turn_summaries": [
                {
                    "turn_id": turn_id,
                    "summary": "用户关注 Java Optional 的安全用法。",
                }
            ]
        }
    )

    context = manager.build_prompt_context(IntentType.GENERAL_CHAT, "Optional")

    assert "近期问答摘要" in context
    assert "用户关注 Java Optional 的安全用法" in context


def test_patch_intents_never_receive_memory_context(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="answer",
    )

    assert manager.build_prompt_context(IntentType.CODE_AUDIT, "检查代码") == ""
    assert manager.build_prompt_context(IntentType.PATCH_EXPLAIN, "解释补丁") == ""
    assert manager.build_prompt_context(IntentType.PATCH_REVISE, "重写补丁") == ""


def test_corrupt_memory_file_is_backed_up_and_ignored(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.memory_file.parent.mkdir(parents=True)
    manager.memory_file.write_text("{bad json", encoding="utf-8")

    memory = manager.load()

    assert memory["working_memory"]["recent_turns"] == []
    assert (manager.memory_file.parent / "memory.corrupt.json").exists()


def test_recent_turns_are_capped(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    for index in range(MAX_RECENT_TURNS + 3):
        manager.append_recent_turn(
            intent=IntentType.GENERAL_CHAT,
            user_text=f"question {index}",
            assistant_text="answer",
        )

    turns = manager.load()["working_memory"]["recent_turns"]
    assert len(turns) == MAX_RECENT_TURNS
    assert turns[0]["user_text"] == "question 3"


def test_invalid_delta_does_not_modify_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="answer",
    )

    assert manager.apply_delta({"turn_summaries": [{"turn_id": "missing", "summary": "bad"}]}) is False
    turn = manager.load()["working_memory"]["recent_turns"][0]
    assert turn["summary_status"] == "pending"
    assert turn["summary"] == ""


def test_long_term_delta_requires_existing_target_id_for_update(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    assert (
        manager.apply_delta(
            {
                "long_term_operations": [
                    {
                        "operation": "update_existing",
                        "target_id": "missing",
                        "type": "durable_preference",
                        "summary": "must not be written",
                    }
                ]
            }
        )
        is False
    )
    assert manager.load()["long_term_memory"]["durable_preferences"] == []


def test_project_fact_requires_valid_project_evidence_id(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    delta = {
        "long_term_operations": [
            {
                "operation": "create_new",
                "type": "project_fact",
                "label": "project identity",
                "summary": "AutoPatch-J 是 Java 代码修复 CLI。",
                "source": "repo_verified",
                "evidence_id": "readme_cn_001",
            }
        ]
    }

    assert manager.apply_delta(delta) is False
    assert manager.apply_delta(delta, allowed_project_evidence_ids={"other"}) is False
    assert manager.apply_delta(delta, allowed_project_evidence_ids={"readme_cn_001"}) is True
    facts = manager.load()["long_term_memory"]["project_facts"]
    assert facts[0]["label"] == "project identity"


def test_project_fact_update_requires_valid_project_evidence_id(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    create_delta = {
        "long_term_operations": [
            {
                "operation": "create_new",
                "type": "project_fact",
                "label": "project identity",
                "summary": "AutoPatch-J 是 Java 代码修复 CLI。",
                "source": "repo_verified",
                "evidence_id": "readme_cn_001",
            }
        ]
    }
    assert manager.apply_delta(create_delta, allowed_project_evidence_ids={"readme_cn_001"}) is True
    fact_id = manager.load()["long_term_memory"]["project_facts"][0]["id"]
    update_delta = {
        "long_term_operations": [
            {
                "operation": "update_existing",
                "target_id": fact_id,
                "summary": "不应写入的项目事实。",
            }
        ]
    }

    assert manager.apply_delta(update_delta) is False
    assert manager.load()["long_term_memory"]["project_facts"][0]["summary"] == "AutoPatch-J 是 Java 代码修复 CLI。"

    update_delta["long_term_operations"][0]["source"] = "repo_verified"
    update_delta["long_term_operations"][0]["evidence_id"] = "readme_cn_001"
    assert manager.apply_delta(update_delta, allowed_project_evidence_ids={"readme_cn_001"}) is True
    assert manager.load()["long_term_memory"]["project_facts"][0]["summary"] == "不应写入的项目事实。"


def test_find_summary_trigger_reports_project_code_explain(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    assert (
        manager.find_summary_trigger(force_project_code_explain=True)
        is MemorySummaryTrigger.PROJECT_CODE_EXPLAIN
    )


def test_clear_resets_memory_file(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="answer",
    )

    manager.clear()

    assert manager.load()["working_memory"]["recent_turns"] == []


def test_summarizer_writes_ready_summary_delta(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="Optional 表达可能为空的值。",
    )
    manager.append_recent_turn(
        intent=IntentType.CODE_EXPLAIN,
        user_text="这个项目是干什么的",
        assistant_text="这是一个 Java CLI 项目。",
    )

    class FakeLLM:
        def __init__(self) -> None:
            self.kwargs = None
            self.messages = None

        def chat(self, messages, **kwargs):
            self.messages = messages
            self.kwargs = kwargs
            payload = json.loads(messages[1]["content"])
            turn_id = payload["pending_turns"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "turn_summaries": [
                            {
                                "turn_id": turn_id,
                                "summary": "用户关注 Java Optional 的空值表达。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    llm = FakeLLM()

    assert MemorySummarizer(manager, llm).try_summarize("这个项目是干什么的") is True

    turns = manager.load()["working_memory"]["recent_turns"]
    assert turns[0]["summary_status"] == "ready"
    assert "Java Optional" in turns[0]["summary"]
    assert llm.kwargs == {
        "tools": None,
        "purpose": LLMCallPurpose.MEMORY_SUMMARY,
    }


def test_summarizer_ignores_invalid_json_delta(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="answer",
    )
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Stream 怎么用",
        assistant_text="answer",
    )

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return SimpleNamespace(content="not json")

    assert MemorySummarizer(manager, FakeLLM()).try_summarize("Stream 怎么用") is False
    assert all(turn["summary_status"] == "pending" for turn in manager.load()["working_memory"]["recent_turns"])


def test_summarizer_allows_project_fact_only_with_payload_evidence(tmp_path: Path) -> None:
    (tmp_path / "README_CN.md").write_text("AutoPatch-J 是面向 Java 仓库的代码修复 CLI。", encoding="utf-8")
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.CODE_EXPLAIN,
        user_text="这个项目是干什么的",
        assistant_text="这是一个 Java 代码修复 CLI。",
    )

    class FakeLLM:
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            evidence_id = payload["project_evidence"][0]["evidence_id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "long_term_operations": [
                            {
                                "operation": "create_new",
                                "type": "project_fact",
                                "label": "project identity",
                                "summary": "AutoPatch-J 是面向 Java 仓库的代码修复 CLI。",
                                "source": "repo_verified",
                                "evidence_id": evidence_id,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    assert (
        MemorySummarizer(manager, FakeLLM(), repo_root=tmp_path).try_summarize(
            trigger=MemorySummaryTrigger.PROJECT_CODE_EXPLAIN,
        )
        is True
    )
    facts = manager.load()["long_term_memory"]["project_facts"]
    assert facts[0]["label"] == "project identity"


def test_summary_scheduler_writes_delta_in_background(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(IntentType.GENERAL_CHAT, "Optional 怎么用", "answer")
    manager.append_recent_turn(IntentType.GENERAL_CHAT, "Stream 怎么用", "answer")

    class FakeLLM:
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            turn_id = payload["pending_turns"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "turn_summaries": [
                            {
                                "turn_id": turn_id,
                                "summary": "用户关注 Java Optional。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    scheduler = MemorySummaryScheduler(manager, FakeLLM(), tmp_path)
    try:
        scheduler.submit_if_needed(MemorySummaryTrigger.PENDING_TURNS, "Stream 怎么用")
        assert _wait_until(lambda: manager.load()["working_memory"]["recent_turns"][0]["summary_status"] == "ready")
    finally:
        scheduler.shutdown(wait=True)


def test_summary_scheduler_discards_result_after_reset(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(IntentType.GENERAL_CHAT, "Optional 怎么用", "answer")
    manager.append_recent_turn(IntentType.GENERAL_CHAT, "Stream 怎么用", "answer")

    class SlowLLM:
        def chat(self, messages, **kwargs):
            time.sleep(0.05)
            payload = json.loads(messages[1]["content"])
            turn_id = payload["pending_turns"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "turn_summaries": [
                            {
                                "turn_id": turn_id,
                                "summary": "这个结果应该被丢弃。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    scheduler = MemorySummaryScheduler(manager, SlowLLM(), tmp_path)
    try:
        scheduler.submit_if_needed(MemorySummaryTrigger.PENDING_TURNS, "Stream 怎么用")
        scheduler.discard_pending_results()
        manager.clear()
        scheduler.shutdown(wait=True)
    finally:
        scheduler.shutdown(wait=True)

    assert manager.load()["working_memory"]["recent_turns"] == []


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
