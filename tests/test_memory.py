from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from autopatch_j.llm.options import LLMCallPurpose
from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory import (
    MAX_RECENT_TURNS,
    MemorySummaryTrigger,
    MemoryManager,
)
from autopatch_j.core.memory.scheduler import MemorySummaryScheduler
from autopatch_j.core.memory.models import MemoryDocument
from autopatch_j.core.memory.prompts import MEMORY_SUMMARY_SYSTEM_PROMPT
from autopatch_j.core.memory.repo_profile import RepoProfileCollector
from autopatch_j.core.memory.summarizer import MemorySummarizer


def _manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(tmp_path / ".autopatch-j" / "memory.json")


def test_missing_memory_file_loads_empty_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    memory = manager.load()

    assert memory["version"] == 1
    assert memory["repo_profile"]["build_tool"] == ""
    assert memory["working_memory"]["recent_turns"] == []
    assert memory["long_term_memory"]["durable_preferences"] == []
    assert memory["long_term_memory"]["project_notes"] == []


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


def test_memory_manager_exposes_typed_document(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="Optional answer",
    )

    document = manager.load_document()

    assert isinstance(document, MemoryDocument)
    assert document.recent_turns[0].intent == IntentType.GENERAL_CHAT.value
    assert document.recent_turns[0].user_text == "Optional 怎么用"


def test_memory_manager_serializes_concurrent_recent_turn_appends(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    def append_turn(index: int) -> None:
        manager.append_recent_turn(
            intent=IntentType.GENERAL_CHAT,
            user_text=f"question {index}",
            assistant_text=f"answer {index}",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append_turn, range(20)))

    turns = manager.load()["working_memory"]["recent_turns"]
    assert len(turns) == MAX_RECENT_TURNS


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


def test_prompt_context_includes_repo_profile_and_project_notes_for_project_questions(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager.apply_delta(
        {
            "long_term_operations": [
                {
                    "operation": "create_new",
                    "type": "project_note",
                    "label": "review module",
                    "summary": "用户关注 review 模块如何管理 finding 队列。",
                    "source": "conversation_summary",
                }
            ]
        },
        repo_profile={
            "build_tool": "maven",
            "java_version": "17",
            "project_name": "demo-service",
            "modules": ["api", "service"],
            "frameworks": ["spring boot"],
            "source_files": ["pom.xml"],
            "updated_at": "2026-05-08T00:00:00+08:00",
        },
    )

    context = manager.build_prompt_context(IntentType.CODE_EXPLAIN, "继续解释这个项目")

    assert "仓库元信息" in context
    assert "构建工具: maven" in context
    assert "项目讨论笔记" in context
    assert "finding 队列" in context


def test_prompt_context_debug_summary_describes_injected_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    assert manager.apply_delta(
        {
            "long_term_operations": [
                {
                    "operation": "create_new",
                    "type": "project_note",
                    "label": "review module",
                    "summary": "用户关注 review 模块如何管理 finding 队列。",
                    "source": "conversation_summary",
                }
            ]
        },
        repo_profile={
            "build_tool": "maven",
            "java_version": "17",
            "project_name": "demo-service",
            "modules": ["api"],
            "frameworks": ["spring boot"],
            "source_files": ["pom.xml"],
            "updated_at": "2026-05-08T00:00:00+08:00",
        },
    )
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="继续聊 review 模块",
        assistant_text="answer",
    )

    summary = manager.build_prompt_context_debug_summary(IntentType.CODE_EXPLAIN, "继续解释这个项目")

    assert "Memory 注入" in summary
    assert "repo_profile: build_tool, java_version, project_name, modules, frameworks" in summary
    assert "project_notes: review module" in summary
    assert "pending_turns: 1 条" in summary


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


def test_mismatched_memory_version_is_ignored_without_migration(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.memory_file.parent.mkdir(parents=True)
    manager.memory_file.write_text(
        json.dumps(
            {
                "version": 0,
                "working_memory": {
                    "recent_turns": [
                        {
                            "id": "turn_old",
                            "intent": "general_chat",
                            "user_text": "版本不匹配问题",
                            "assistant_text": "版本不匹配回答",
                        }
                    ]
                },
                "long_term_memory": {
                    "project_facts": [
                        {
                            "id": "mem_old",
                            "type": "project_fact",
                            "label": "old",
                            "summary": "版本不匹配项目事实",
                        }
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    memory = manager.load()

    assert memory["version"] == 1
    assert memory["working_memory"]["recent_turns"] == []
    assert memory["long_term_memory"]["project_notes"] == []
    assert not (manager.memory_file.parent / "memory.corrupt.json").exists()


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
    document = manager.load_document()
    assert len(document.recent_turns) == MAX_RECENT_TURNS
    assert document.recent_turns[0].user_text == "question 3"


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


def test_project_note_requires_conversation_summary_source(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    invalid_delta = {
        "long_term_operations": [
            {
                "operation": "create_new",
                "type": "project_note",
                "label": "review module",
                "summary": "用户正在关注 review 模块的职责边界。",
                "source": "user_explicit",
            }
        ]
    }
    valid_delta = {
        "long_term_operations": [
            {
                "operation": "create_new",
                "type": "project_note",
                "label": "review module",
                "summary": "用户正在关注 review 模块的职责边界。",
                "source": "conversation_summary",
            }
        ]
    }

    assert manager.apply_delta(invalid_delta) is False
    assert manager.apply_delta(valid_delta) is True
    notes = manager.load()["long_term_memory"]["project_notes"]
    assert notes[0]["label"] == "review module"


def test_project_note_update_requires_conversation_summary_source(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    create_delta = {
        "long_term_operations": [
            {
                "operation": "create_new",
                "type": "project_note",
                "label": "review module",
                "summary": "用户正在关注 review 模块的职责边界。",
                "source": "conversation_summary",
            }
        ]
    }
    assert manager.apply_delta(create_delta) is True
    note_id = manager.load()["long_term_memory"]["project_notes"][0]["id"]
    update_delta = {
        "long_term_operations": [
            {
                "operation": "update_existing",
                "target_id": note_id,
                "summary": "用户继续关注 review 模块与补丁确认的关系。",
            }
        ]
    }

    assert manager.apply_delta(update_delta) is False
    assert manager.load()["long_term_memory"]["project_notes"][0]["summary"] == "用户正在关注 review 模块的职责边界。"

    update_delta["long_term_operations"][0]["source"] = "conversation_summary"
    assert manager.apply_delta(update_delta) is True
    assert manager.load()["long_term_memory"]["project_notes"][0]["summary"] == "用户继续关注 review 模块与补丁确认的关系。"


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


def test_repo_profile_collector_extracts_narrow_maven_metadata(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        """
        <project>
          <artifactId>demo-service</artifactId>
          <properties>
            <java.version>17</java.version>
          </properties>
          <modules>
            <module>api</module>
            <module>service</module>
          </modules>
          <dependencies>
            <dependency>
              <groupId>org.springframework.boot</groupId>
              <artifactId>spring-boot-starter-web</artifactId>
            </dependency>
          </dependencies>
        </project>
        """,
        encoding="utf-8",
    )

    profile = RepoProfileCollector(tmp_path).collect()

    assert profile["build_tool"] == "maven"
    assert profile["java_version"] == "17"
    assert profile["project_name"] == "demo-service"
    assert profile["modules"] == ["api", "service"]
    assert "spring boot" in profile["frameworks"]
    assert profile["source_files"] == ["pom.xml"]


def test_repo_profile_collector_does_not_invent_business_identity_from_gradle(tmp_path: Path) -> None:
    (tmp_path / "settings.gradle").write_text(
        """
        rootProject.name = 'demo-platform'
        include ':api', ':service'
        """,
        encoding="utf-8",
    )
    (tmp_path / "build.gradle").write_text(
        """
        plugins {
            id 'java'
        }
        java {
            toolchain {
                languageVersion = JavaLanguageVersion.of(21)
            }
        }
        """,
        encoding="utf-8",
    )

    profile = RepoProfileCollector(tmp_path).collect()

    assert profile["build_tool"] == "gradle"
    assert profile["java_version"] == "21"
    assert profile["project_name"] == "demo-platform"
    assert profile["modules"] == ["api", "service"]
    assert profile["frameworks"] == []


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


def test_summarizer_writes_repo_profile_and_project_note(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text(
        """
        <project>
          <artifactId>demo-service</artifactId>
          <properties><java.version>17</java.version></properties>
        </project>
        """,
        encoding="utf-8",
    )
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.CODE_EXPLAIN,
        user_text="这个项目的 review 模块是什么职责",
        assistant_text="review 模块负责 finding 队列和补丁确认。",
    )

    class FakeLLM:
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            assert payload["repo_profile"]["build_tool"] == "maven"
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "long_term_operations": [
                            {
                                "operation": "create_new",
                                "type": "project_note",
                                "label": "review module",
                                "summary": "用户关注 review 模块如何管理 finding 队列和补丁确认。",
                                "source": "conversation_summary",
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
    memory = manager.load()
    assert memory["repo_profile"]["build_tool"] == "maven"
    assert memory["repo_profile"]["java_version"] == "17"
    notes = memory["long_term_memory"]["project_notes"]
    assert notes[0]["label"] == "review module"


def test_memory_summary_prompt_describes_project_notes() -> None:
    assert "project_note 只记录用户围绕当前仓库持续讨论出来的上下文" in MEMORY_SUMMARY_SYSTEM_PROMPT
    assert "不要把 repo_profile 中的构建信息改写成业务事实" in MEMORY_SUMMARY_SYSTEM_PROMPT
    assert "不适用字段请省略" in MEMORY_SUMMARY_SYSTEM_PROMPT


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
