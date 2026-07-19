from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from autopatch_j.core.memory import MemoryManager
from autopatch_j.core.memory.models import (
    CandidateSource,
    ConsolidationOperation,
    ConsolidationResult,
    ExtractionCandidateInput,
    ExtractionResult,
    MemoryDetail,
    MemorySource,
    MemorySummarySnapshot,
)
from autopatch_j.core.memory.store import MemoryStore
from autopatch_j.core.memory.summary_projection import (
    MEMORY_SUMMARY_HEADER,
    MemorySummaryProjector,
)


class CapturingEmptyMemoryLLM:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.called = Event()

    def chat(self, messages, tools=None, purpose=None):
        del tools, purpose
        self.prompts.extend(str(message["content"]) for message in messages)
        self.called.set()
        return SimpleNamespace(
            content=json.dumps(
                {
                    "thread_compaction": "当前 thread 正在讨论摘要隔离",
                    "candidates": [],
                },
                ensure_ascii=False,
            )
        )


def _create_active_item(
    store: MemoryStore,
    *,
    owner: str,
    kind: str,
    subject: str,
    statement: str,
    source_texts: tuple[str, ...],
) -> str:
    strength = "soft" if kind == "discussion_context" else "hard"
    recall_mode = "on_match" if kind == "discussion_context" else "always"
    sources: list[CandidateSource] = []
    for index, source_text in enumerate(source_texts):
        turn_owner = f"turn-{owner}-{index}"
        turn = store.begin_turn(
            "general_chat",
            source_text,
            turn_owner,
            ["src/main/java/demo"],
        )
        store.complete_turn(turn.id, "收到", turn_owner)
        sources.append(CandidateSource(turn.id, "user", source_text))

    extraction = store.claim_extraction_batch(f"extract-{owner}", force=True)
    assert extraction is not None
    candidate_ids = store.complete_extraction(
        extraction,
        ExtractionResult(
            thread_compaction=f"checkpoint-{owner}",
            candidates=(
                ExtractionCandidateInput(
                    kind=kind,
                    subject=subject,
                    statement=statement,
                    content=f"完整内容：{statement}",
                    strength=strength,
                    origin="explicit",
                    recall_mode=recall_mode,
                    applies_to_paths=("src/main/java/demo",),
                    aliases=(subject,),
                    keywords=("memory", owner),
                    sources=tuple(sources),
                ),
            ),
        ),
    )
    consolidation = store.claim_consolidation_job(
        f"consolidate-{owner}", force=True
    )
    assert consolidation is not None
    item_ids = store.apply_consolidation(
        consolidation,
        ConsolidationResult(
            operations=(
                ConsolidationOperation(
                    operation="create",
                    candidate_ids=candidate_ids,
                    target_id=None,
                    kind=kind,
                    subject=subject,
                    statement=statement,
                    content=f"完整内容：{statement}",
                    strength=strength,
                    origin="explicit",
                    recall_mode=recall_mode,
                    applies_to_paths=("src/main/java/demo",),
                    aliases=(subject,),
                    keywords=("memory", owner),
                ),
            )
        ),
    )
    assert len(item_ids) == 1
    return item_ids[0]


def test_renderer_has_fixed_header_and_bounded_review_content() -> None:
    sources = tuple(
        MemorySource(
            turn_id=f"turn-{index}",
            role="user",
            quote=(f"source-{index}-" + "x" * 900),
            created_at=f"2026-07-19T00:00:0{index}+00:00",
        )
        for index in range(1, 5)
    )
    detail = MemoryDetail(
        id="memory-current",
        logical_id="project_decision:java_runtime",
        revision=2,
        kind="project_decision",
        thread_id=None,
        subject="Java runtime",
        statement="项目采用 Java 21",
        content="构建、测试与运行统一采用 Java 21。",
        strength="hard",
        origin="explicit",
        recall_mode="always",
        applies_to_paths=("src/main/java",),
        aliases=("JDK",),
        keywords=("java", "runtime"),
        status="active",
        sources=sources,
        access_count=99,
        last_accessed_at="2026-07-19T01:00:00+00:00",
        updated_at="2026-07-19T00:00:00+00:00",
    )
    snapshot = MemorySummarySnapshot(
        active_thread_id="thread-current",
        thread_checkpoint="正在确认项目运行时基线。",
        items=(detail,),
    )

    rendered = MemorySummaryProjector.render(
        snapshot,
        projected_at="2026-07-19T02:00:00+00:00",
    )

    assert rendered.splitlines()[0] == MEMORY_SUMMARY_HEADER
    assert "项目采用 Java 21" in rendered
    assert "构建、测试与运行统一采用 Java 21。" in rendered
    assert "project_decision:java_runtime" in rendered
    assert "source-1" in rendered
    assert "source-3" in rendered
    assert "source-4" not in rendered
    assert "access_count" not in rendered
    assert "99" not in rendered


def test_store_snapshot_is_consistent_bounded_and_current_thread_scoped(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    project_item_id = _create_active_item(
        store,
        owner="project",
        kind="project_decision",
        subject="Java runtime",
        statement="项目采用 Java 21",
        source_texts=(
            "采用 Java 21",
            "确认 Java 21",
            "CI 使用 Java 21",
            "运行环境也是 Java 21" + "长" * 1_000,
        ),
    )
    _create_active_item(
        store,
        owner="discussion",
        kind="discussion_context",
        subject="current review",
        statement="当前正在讨论 review 投影",
        source_texts=("继续讨论 review 投影",),
    )

    before_switch = store.summary_snapshot()
    old_thread_id = before_switch.active_thread_id
    project_item = next(item for item in before_switch.items if item.id == project_item_id)

    assert len(project_item.sources) == 3
    assert all(len(source.quote) <= 800 for source in project_item.sources)
    assert any(item.kind == "discussion_context" for item in before_switch.items)
    assert project_item.updated_at

    store.start_new_thread(expected_thread_id=old_thread_id)
    after_switch = store.summary_snapshot()

    assert after_switch.active_thread_id != old_thread_id
    assert [item.id for item in after_switch.items] == [project_item_id]
    assert after_switch.thread_checkpoint == ""


def test_manager_refreshes_for_forget_clear_and_thread_switch(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    item_id = _create_active_item(
        manager.store,
        owner="lifecycle",
        kind="user_preference",
        subject="answer style",
        statement="回答保持简洁",
        source_texts=("以后回答保持简洁",),
    )

    first = manager.rebuild_summary()
    assert first.status.state == "current"
    assert "回答保持简洁" in manager.summary_path.read_text(encoding="utf-8")

    forgotten = manager.forget(item_id)
    assert forgotten.forgotten
    assert manager.summary_status().state == "current"
    assert "回答保持简洁" not in manager.summary_path.read_text(encoding="utf-8")

    old_thread = manager.ensure_active_thread()
    new_thread = manager.start_new_thread(expected_thread_id=old_thread.id)
    assert new_thread.id in manager.summary_path.read_text(encoding="utf-8")

    manager.clear()
    cleared = manager.summary_path.read_text(encoding="utf-8")
    assert manager.summary_status().state == "current"
    assert "Active items：0" in cleared
    assert "暂无 checkpoint。" in cleared


def test_projection_failure_keeps_database_and_last_good_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    initial = manager.rebuild_summary()
    assert initial.status.state == "current"
    old_thread = manager.ensure_active_thread()
    last_good = manager.summary_path.read_bytes()

    def fail_write(_payload: str) -> None:
        raise OSError("projection disk unavailable")

    monkeypatch.setattr(manager._summary_projector, "_write_atomic", fail_write)
    new_thread = manager.start_new_thread(expected_thread_id=old_thread.id)

    failed_status = manager.summary_status()
    assert failed_status.state == "stale"
    assert "projection disk unavailable" in failed_status.last_error
    assert manager.summary_path.read_bytes() == last_good
    assert manager.status().healthy
    assert manager.ensure_active_thread().id == new_thread.id

    monkeypatch.undo()
    recovered = manager.rebuild_summary()
    assert recovered.status.state == "current"
    assert new_thread.id in manager.summary_path.read_text(encoding="utf-8")

    manager.summary_path.write_text(
        manager.summary_path.read_text(encoding="utf-8")
        + "\nMANUAL_SUMMARY_SENTINEL\n",
        encoding="utf-8",
    )
    assert manager.summary_status().state == "stale"
    manager.rebuild_summary()
    assert "MANUAL_SUMMARY_SENTINEL" not in manager.summary_path.read_text(
        encoding="utf-8"
    )


def test_markdown_sentinel_never_enters_memory_llm_or_sqlite_recall(
    tmp_path: Path,
) -> None:
    llm = CapturingEmptyMemoryLLM()
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    manager.rebuild_summary()
    sentinel = "SUMMARY_ONLY_SENTINEL_MUST_NOT_REACH_LLM"
    manager.summary_path.write_text(
        manager.summary_path.read_text(encoding="utf-8") + f"\n{sentinel}\n",
        encoding="utf-8",
    )

    turn = manager.begin_turn(
        intent="general_chat",
        user_text="讨论摘要隔离",
    )
    manager.complete_turn(turn.id, assistant_text="继续")
    result = manager.flush_once("summary-isolation")

    assert result.succeeded == 1
    assert llm.prompts
    assert sentinel not in "\n".join(llm.prompts)
    assert sentinel not in str(manager.build_thread_history(max_tokens=10_000))
    assert sentinel not in manager.active_thread_checkpoint(max_tokens=10_000)
    assert sentinel not in manager.summary_path.read_text(encoding="utf-8")


def test_start_rebuilds_summary_and_background_worker_refreshes_it(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "memory_summary.md"
    summary_path.write_text("MANUAL_OR_STALE_FILE", encoding="utf-8")
    llm = CapturingEmptyMemoryLLM()
    manager = MemoryManager(db_path=tmp_path / "memory.db", llm=llm)
    turn = manager.begin_turn(intent="general_chat", user_text="后台刷新投影")
    manager.complete_turn(turn.id, assistant_text="收到")
    second_turn = manager.begin_turn(
        intent="general_chat",
        user_text="第二个 turn 触发 extraction batch",
    )
    manager.complete_turn(second_turn.id, assistant_text="继续")

    manager.start()
    try:
        assert manager.summary_path.read_text(encoding="utf-8").startswith(
            MEMORY_SUMMARY_HEADER
        )
        assert llm.called.wait(timeout=3)
        deadline = monotonic() + 3
        while monotonic() < deadline:
            if "当前 thread 正在讨论摘要隔离" in manager.summary_path.read_text(
                encoding="utf-8"
            ):
                break
            sleep(0.02)
        else:
            pytest.fail("background Memory pipeline 未刷新审阅投影")
    finally:
        manager.close()


def test_projector_uses_utf8_atomic_file_and_semantic_skip(tmp_path: Path) -> None:
    projected_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    projector = MemorySummaryProjector(
        tmp_path / "memory_summary.md",
        clock=lambda: projected_at,
    )
    snapshot = MemorySummarySnapshot(
        active_thread_id="thread-empty",
        thread_checkpoint="",
        items=(),
    )

    assert projector.status().state == "missing"

    first = projector.refresh(snapshot)
    second = projector.refresh(snapshot)

    assert first.changed is True
    assert second.changed is False
    assert projector.path.read_bytes().decode("utf-8").startswith(
        MEMORY_SUMMARY_HEADER
    )
    assert list(tmp_path.glob(".memory_summary.md.*.tmp")) == []
