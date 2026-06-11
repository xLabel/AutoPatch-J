from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory import (
    MAX_EPISODES,
    MemoryManager,
    MemorySummaryTrigger,
)
from autopatch_j.core.memory.models import MemoryDocument
from autopatch_j.core.memory.prompts import MEMORY_SUMMARY_SYSTEM_PROMPT
from autopatch_j.core.memory.repo_profile import RepoProfileCollector
from autopatch_j.core.memory.scheduler import MemorySummaryScheduler
from autopatch_j.core.memory.summarizer import MemorySummarizer
from autopatch_j.llm.options import LLMCallPurpose


def _manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(tmp_path / ".autopatch-j" / "memory.json")


def _append_episode(manager: MemoryManager, user_text: str = "Optional 怎么用") -> str:
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text=user_text,
        assistant_text="Optional 表达可能为空的值。",
    )
    return manager.load()["episodic_memory"]["episodes"][-1]["id"]


def test_missing_memory_file_loads_empty_context_engine_schema(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    memory = manager.load()

    assert memory["version"] == 1
    assert memory["working_memory"] == {"active_topics": [], "pending_episode_ids": []}
    assert memory["episodic_memory"]["episodes"] == []
    assert memory["semantic_memory"] == {
        "user_preferences": [],
        "project_notes": [],
        "codebase_concepts": [],
    }
    assert memory["procedural_memory"] == {"collaboration_preferences": []}
    assert memory["repo_profile"]["build_tool"] == ""


def test_append_recent_turn_writes_pending_episode(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    episode_id = _append_episode(manager, "这个项目是干什么的")

    memory = json.loads(manager.memory_file.read_text(encoding="utf-8"))
    episode = memory["episodic_memory"]["episodes"][0]
    assert episode["id"] == episode_id
    assert episode["intent"] == "general_chat"
    assert episode["user_goal"] == "这个项目是干什么的"
    assert episode["summary_status"] == "pending"
    assert memory["working_memory"]["pending_episode_ids"] == [episode_id]


def test_memory_manager_exposes_typed_document(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager)

    document = manager.load_document()

    assert isinstance(document, MemoryDocument)
    assert document.episodes[0].intent == IntentType.GENERAL_CHAT.value
    assert document.episodes[0].user_goal == "Optional 怎么用"


def test_memory_manager_serializes_concurrent_episode_appends(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    def append_episode(index: int) -> None:
        manager.append_recent_turn(
            intent=IntentType.GENERAL_CHAT,
            user_text=f"question {index}",
            assistant_text=f"answer {index}",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append_episode, range(MAX_EPISODES + 5)))

    episodes = manager.load()["episodic_memory"]["episodes"]
    assert len(episodes) == MAX_EPISODES
    assert episodes[0]["user_goal"] == "question 5"


def test_prompt_context_body_uses_pending_user_text_but_not_assistant_text(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.append_recent_turn(
        intent=IntentType.GENERAL_CHAT,
        user_text="Optional 怎么用",
        assistant_text="assistant answer should stay out of prompt",
    )

    context = manager.build_prompt_context(IntentType.CODE_EXPLAIN, "继续讲项目代码")

    assert "## Memory Context" not in context
    assert "待摘要用户输入" in context
    assert "Optional 怎么用" in context
    assert "assistant answer should stay out of prompt" not in context


def test_prompt_context_includes_markdown_episode_summary(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    episode_id = _append_episode(manager)

    assert manager.apply_delta(
        {
            "episode_summaries": [
                {
                    "episode_id": episode_id,
                    "summary": "用户关注 Java Optional 的安全用法。",
                }
            ]
        }
    )

    context = manager.build_prompt_context(IntentType.GENERAL_CHAT, "Optional")

    assert "### 相关经历摘要" in context
    assert "用户关注 Java Optional 的安全用法" in context


def test_prompt_context_includes_repo_profile_and_semantic_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    episode_id = _append_episode(manager, "继续解释 review 模块")
    assert manager.apply_delta(
        {
            "episode_summaries": [
                {"episode_id": episode_id, "summary": "用户关注 review 模块职责。"}
            ],
            "semantic_operations": [
                {
                    "operation": "create_new",
                    "type": "project_note",
                    "label": "review module",
                    "summary": "用户关注 review 模块如何管理 finding 队列。",
                    "source_episode_ids": [episode_id],
                    "confidence": "high",
                }
            ],
            "procedural_operations": [
                {
                    "operation": "create_new",
                    "type": "collaboration_preference",
                    "label": "answer style",
                    "summary": "用户偏好中文、直接、工程化的回答。",
                    "source_episode_ids": [episode_id],
                    "confidence": "high",
                }
            ],
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

    context = manager.build_prompt_context(IntentType.CODE_EXPLAIN, "继续解释这个项目的 review 模块")

    assert "### 用户协作偏好" in context
    assert "中文、直接、工程化" in context
    assert "### 当前项目画像" in context
    assert "- build tool: maven" in context
    assert "### 相关项目理解" in context
    assert "finding 队列" in context


def test_prompt_context_debug_summary_describes_injected_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    episode_id = _append_episode(manager, "继续解释 review 模块")
    manager.apply_delta(
        {
            "semantic_operations": [
                {
                    "operation": "create_new",
                    "type": "project_note",
                    "label": "review module",
                    "summary": "用户关注 review 模块职责。",
                    "source_episode_ids": [episode_id],
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

    summary = manager.build_prompt_context_debug_summary(IntentType.CODE_EXPLAIN, "继续解释这个项目")

    assert "Memory 注入" in summary
    assert "repo_profile: build_tool, java_version, project_name, modules, frameworks" in summary
    assert "semantic_memory: review module" in summary
    assert "pending_inputs: 1 条" in summary


def test_patch_intents_never_receive_memory_context(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager)

    assert manager.build_prompt_context(IntentType.CODE_AUDIT, "检查代码") == ""
    assert manager.build_prompt_context(IntentType.PATCH_EXPLAIN, "解释补丁") == ""
    assert manager.build_prompt_context(IntentType.PATCH_REVISE, "重写补丁") == ""


def test_corrupt_memory_file_is_backed_up_and_ignored(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.memory_file.parent.mkdir(parents=True)
    manager.memory_file.write_text("{bad json", encoding="utf-8")

    memory = manager.load()

    assert memory["episodic_memory"]["episodes"] == []
    assert (manager.memory_file.parent / "memory.corrupt.json").exists()


def test_mismatched_memory_version_is_ignored_without_migration(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.memory_file.parent.mkdir(parents=True)
    manager.memory_file.write_text(
        json.dumps(
            {
                "version": 0,
                "working_memory": {"recent_turns": [{"id": "turn_old"}]},
                "long_term_memory": {"project_notes": [{"id": "old"}]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    memory = manager.load()

    assert memory["version"] == 1
    assert memory["episodic_memory"]["episodes"] == []
    assert memory["semantic_memory"]["project_notes"] == []
    assert not (manager.memory_file.parent / "memory.corrupt.json").exists()


def test_invalid_delta_does_not_modify_memory(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    episode_id = _append_episode(manager)

    assert manager.apply_delta({"episode_summaries": [{"episode_id": "missing", "summary": "bad"}]}) is False
    episode = manager.load()["episodic_memory"]["episodes"][0]
    assert episode["id"] == episode_id
    assert episode["summary_status"] == "pending"
    assert episode["summary"] == ""


def test_long_term_memory_requires_valid_source_episode_ids(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    episode_id = _append_episode(manager)

    invalid_delta = {
        "semantic_operations": [
            {
                "operation": "create_new",
                "type": "project_note",
                "label": "review module",
                "summary": "用户关注 review 模块职责。",
                "source_episode_ids": ["missing"],
            }
        ]
    }
    valid_delta = {
        "semantic_operations": [
            {
                "operation": "create_new",
                "type": "project_note",
                "label": "review module",
                "summary": "用户关注 review 模块职责。",
                "source_episode_ids": [episode_id],
            }
        ]
    }

    assert manager.apply_delta(invalid_delta) is False
    assert manager.apply_delta(valid_delta) is True
    notes = manager.load()["semantic_memory"]["project_notes"]
    assert notes[0]["source_episode_ids"] == [episode_id]


def test_long_term_update_requires_valid_source_episode_ids(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    episode_id = _append_episode(manager)
    assert manager.apply_delta(
        {
            "semantic_operations": [
                {
                    "operation": "create_new",
                    "type": "project_note",
                    "label": "review module",
                    "summary": "用户关注 review 模块职责。",
                    "source_episode_ids": [episode_id],
                }
            ]
        }
    )
    note_id = manager.load()["semantic_memory"]["project_notes"][0]["id"]

    assert (
        manager.apply_delta(
            {
                "semantic_operations": [
                    {
                        "operation": "update_existing",
                        "target_id": note_id,
                        "summary": "这次更新不应写入。",
                        "source_episode_ids": ["missing"],
                    }
                ]
            }
        )
        is False
    )
    assert manager.load()["semantic_memory"]["project_notes"][0]["summary"] == "用户关注 review 模块职责。"


def test_find_summary_trigger_reports_project_code_explain(tmp_path: Path) -> None:
    manager = _manager(tmp_path)

    assert (
        manager.find_summary_trigger(force_project_code_explain=True)
        is MemorySummaryTrigger.PROJECT_CODE_EXPLAIN
    )


def test_clear_resets_memory_file(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager)

    manager.clear()

    assert manager.load()["episodic_memory"]["episodes"] == []


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


def test_summarizer_writes_episode_and_semantic_delta(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager, "Optional 怎么用")
    _append_episode(manager, "这个项目是干什么的")

    class FakeLLM:
        def __init__(self) -> None:
            self.kwargs = None

        def chat(self, messages, **kwargs):
            self.kwargs = kwargs
            payload = json.loads(messages[1]["content"])
            episode_id = payload["pending_episodes"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "episode_summaries": [
                            {
                                "episode_id": episode_id,
                                "summary": "用户关注 Java Optional 的空值表达。",
                            }
                        ],
                        "semantic_operations": [
                            {
                                "operation": "create_new",
                                "type": "user_preference",
                                "label": "java optional",
                                "summary": "用户关注 Optional 的安全用法。",
                                "source_episode_ids": [episode_id],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
            )

    llm = FakeLLM()

    assert MemorySummarizer(manager, llm).try_summarize("这个项目是干什么的") is True

    memory = manager.load()
    assert memory["episodic_memory"]["episodes"][0]["summary_status"] == "ready"
    assert memory["semantic_memory"]["user_preferences"][0]["label"] == "java optional"
    assert llm.kwargs == {
        "tools": None,
        "purpose": LLMCallPurpose.MEMORY_SUMMARY,
    }


def test_summarizer_ignores_invalid_json_delta(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager, "Optional 怎么用")
    _append_episode(manager, "Stream 怎么用")

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return SimpleNamespace(content="not json")

    assert MemorySummarizer(manager, FakeLLM()).try_summarize("Stream 怎么用") is False
    assert all(
        episode["summary_status"] == "pending"
        for episode in manager.load()["episodic_memory"]["episodes"]
    )


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
    _append_episode(manager, "这个项目的 review 模块是什么职责")

    class FakeLLM:
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            assert payload["repo_profile"]["build_tool"] == "maven"
            episode_id = payload["pending_episodes"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "semantic_operations": [
                            {
                                "operation": "create_new",
                                "type": "project_note",
                                "label": "review module",
                                "summary": "用户关注 review 模块如何管理 finding 队列和补丁确认。",
                                "source_episode_ids": [episode_id],
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
    assert memory["semantic_memory"]["project_notes"][0]["label"] == "review module"


def test_memory_summary_prompt_describes_context_engine_delta() -> None:
    assert "pending episodes" in MEMORY_SUMMARY_SYSTEM_PROMPT
    assert "source_episode_ids" in MEMORY_SUMMARY_SYSTEM_PROMPT
    assert "不要把 repo_profile 里的构建信息改写成业务事实" in MEMORY_SUMMARY_SYSTEM_PROMPT


def test_summary_scheduler_writes_delta_in_background(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager, "Optional 怎么用")
    _append_episode(manager, "Stream 怎么用")

    class FakeLLM:
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            episode_id = payload["pending_episodes"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "episode_summaries": [
                            {
                                "episode_id": episode_id,
                                "summary": "用户关注 Java Optional。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    scheduler = MemorySummaryScheduler(manager, FakeLLM(), tmp_path)
    try:
        scheduler.submit_if_needed(MemorySummaryTrigger.PENDING_EPISODES, "Stream 怎么用")
        assert _wait_until(
            lambda: manager.load()["episodic_memory"]["episodes"][0]["summary_status"] == "ready"
        )
    finally:
        scheduler.shutdown(wait=True)


def test_summary_scheduler_discards_result_after_reset(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _append_episode(manager, "Optional 怎么用")
    _append_episode(manager, "Stream 怎么用")

    class SlowLLM:
        def chat(self, messages, **kwargs):
            time.sleep(0.05)
            payload = json.loads(messages[1]["content"])
            episode_id = payload["pending_episodes"][0]["id"]
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "episode_summaries": [
                            {
                                "episode_id": episode_id,
                                "summary": "这个结果应该被丢弃。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    scheduler = MemorySummaryScheduler(manager, SlowLLM(), tmp_path)
    try:
        scheduler.submit_if_needed(MemorySummaryTrigger.PENDING_EPISODES, "Stream 怎么用")
        scheduler.discard_pending_results()
        manager.clear()
        scheduler.shutdown(wait=True)
    finally:
        scheduler.shutdown(wait=True)

    assert manager.load()["episodic_memory"]["episodes"] == []


def _wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()
