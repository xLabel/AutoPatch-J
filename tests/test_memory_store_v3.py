from __future__ import annotations

import sqlite3
import json
from pathlib import Path

from autopatch_j.core.memory import MemoryManager, RecallPolicy, RecallQuery
from autopatch_j.core.memory.constants import MAX_SCOPE_PATHS
from autopatch_j.core.memory.errors import MemoryContractError, MemoryNotFoundError
from autopatch_j.core.memory.models import (
    CandidateSource,
    ConsolidationOperation,
    ConsolidationResult,
    ExtractionCandidateInput,
    ExtractionResult,
)
from autopatch_j.core.memory.store import MemoryStore
import pytest


def _extract(
    store: MemoryStore,
    *,
    user_text: str,
    kind: str,
    subject: str,
    statement: str,
    owner: str,
    strength: str = "hard",
    origin: str = "explicit",
    recall_mode: str = "always",
    paths: tuple[str, ...] = (),
    aliases: tuple[str, ...] | None = None,
    keywords: tuple[str, ...] = ("runtime",),
    content: str | None = None,
) -> tuple[str, tuple[str, ...]]:
    turn = store.begin_turn("general_chat", user_text, f"turn-{owner}", paths)
    store.complete_turn(turn.id, "收到", f"turn-{owner}")
    batch = store.claim_extraction_batch(f"extract-{owner}", force=True)
    assert batch is not None
    candidate = ExtractionCandidateInput(
        kind=kind,
        subject=subject,
        statement=statement,
        content=content or statement,
        strength=strength,
        origin=origin,
        recall_mode=recall_mode,
        applies_to_paths=paths,
        aliases=aliases or (subject,),
        keywords=keywords,
        sources=(CandidateSource(turn.id, "user", user_text),),
    )
    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(thread_compaction=statement, candidates=(candidate,)),
    )
    assert len(candidate_ids) == 1
    return turn.id, candidate_ids


def _apply(
    store: MemoryStore,
    *,
    candidate_ids: tuple[str, ...],
    operation: str,
    target_id: str | None,
    kind: str,
    subject: str,
    statement: str,
    owner: str,
    strength: str = "hard",
    origin: str = "explicit",
    recall_mode: str = "always",
    paths: tuple[str, ...] = (),
    aliases: tuple[str, ...] | None = None,
    keywords: tuple[str, ...] = ("runtime",),
    content: str | None = None,
) -> str:
    batch = store.claim_consolidation_job(f"consolidate-{owner}", force=True)
    assert batch is not None
    item_ids = store.apply_consolidation(
        batch,
        ConsolidationResult(
            operations=(
                ConsolidationOperation(
                    operation=operation,
                    candidate_ids=candidate_ids,
                    target_id=target_id,
                    kind=kind,
                    subject=subject,
                    statement=statement,
                    content=content or statement,
                    strength=strength,
                    origin=origin,
                    recall_mode=recall_mode,
                    applies_to_paths=paths,
                    aliases=aliases or (subject,),
                    keywords=keywords,
                ),
            )
        ),
    )
    assert len(item_ids) == 1
    return item_ids[0]


def test_v3_initialization_keeps_legacy_json_untouched(tmp_path: Path) -> None:
    legacy = tmp_path / "memory.json"
    legacy.write_text('{"version": 1}', encoding="utf-8")

    manager = MemoryManager(db_path=tmp_path / "memory.db")

    assert legacy.read_text(encoding="utf-8") == '{"version": 1}'
    assert manager.status().schema_version == 3
    with sqlite3.connect(tmp_path / "memory.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def test_existing_non_v3_database_is_degraded_and_not_modified(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("CREATE TABLE legacy(value TEXT)")
        connection.execute("INSERT INTO legacy(value) VALUES ('keep-me')")
        connection.execute("PRAGMA user_version=2")
    before = db_path.read_bytes()

    manager = MemoryManager(db_path=db_path)

    status = manager.status()
    assert status.degraded is True
    assert status.schema_version == 0
    assert db_path.read_bytes() == before

    exported = manager.export(tmp_path / "exports")
    payload = json.loads(exported.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["legacy"] == [{"value": "keep-me"}]
    assert db_path.read_bytes() == before


def test_degraded_v3_bootstrap_still_allows_show_export_and_explicit_clear(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    store = MemoryStore(db_path)
    _, candidates = _extract(
        store,
        user_text="项目决定采用 Java 21 runtime",
        kind="project_decision",
        subject="java runtime",
        statement="项目采用 Java 21 runtime",
        owner="bootstrap",
    )
    item_id = _apply(
        store,
        candidate_ids=candidates,
        operation="create",
        target_id=None,
        kind="project_decision",
        subject="java runtime",
        statement="项目采用 Java 21 runtime",
        owner="bootstrap",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute("DELETE FROM memory_meta")

    manager = MemoryManager(db_path=db_path)

    assert manager.status().degraded
    assert manager.show_item(item_id).statement == "项目采用 Java 21 runtime"
    exported = manager.export(tmp_path / "exports")
    payload = json.loads(exported.path.read_text(encoding="utf-8"))
    assert payload["memory_meta"] == []
    assert any(item["id"] == item_id for item in payload["memory_items"])

    clear = manager.clear()
    assert clear.active_thread_id
    assert manager.status().healthy


def test_recent_history_uses_token_budget_and_excludes_repair_turns(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    ordinary = store.begin_turn("general_chat", "old ordinary " * 100, "ordinary")
    store.complete_turn(ordinary.id, "old answer " * 100, "ordinary")
    repair = store.begin_turn(
        "patch_revise",
        "不要使用三元表达式",
        "repair",
        ["A.java"],
    )
    store.complete_turn(repair.id, "已修订补丁", "repair")
    latest = store.begin_turn("general_chat", "latest question", "latest")
    store.complete_turn(latest.id, "latest answer", "latest")

    history = store.build_thread_history(max_tokens=40)

    assert history == [
        {"role": "user", "content": "latest question"},
        {"role": "assistant", "content": "latest answer"},
    ]
    assert "三元表达式" not in str(history)


def test_turn_scope_paths_are_bounded_before_persistence_and_extraction(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    scope_paths = [f"src/main/java/demo/File{index}.java" for index in range(15)]
    turn = store.begin_turn("code_audit", "audit project", "turn", scope_paths)
    completed = store.complete_turn(turn.id, "done", "turn")
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None

    payload = store.extraction_payload(batch)

    assert completed.scope_paths == tuple(scope_paths[:MAX_SCOPE_PATHS])
    assert payload["turns"][0]["scope_paths"] == scope_paths[:MAX_SCOPE_PATHS]
    assert len(scope_paths) == 15


def test_memory_map_refresh_preserves_spent_recall_budget(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    thread = manager.ensure_active_thread()
    query = RecallQuery(
        intent="general_chat",
        thread_id=thread.id,
        user_text="unmatched",
    )
    policy = manager.build_recall_policy(
        intent="general_chat",
        thread_id=thread.id,
        durable_token_budget=100,
        map_token_budget=40,
    )
    state = manager.open_memory_request(query, policy)
    state.remaining_tokens = 60

    refreshed = manager.refresh_memory_request(state)

    assert refreshed.estimated_tokens <= 40
    assert state.remaining_tokens == 60 - refreshed.estimated_tokens


def test_store_owns_logical_id_and_cross_kind_revision_identity(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    first_turn, first_candidates = _extract(
        store,
        user_text="以后默认偏好 Java 21 runtime",
        kind="user_preference",
        subject="java runtime",
        statement="项目默认使用 Java 21 runtime",
        owner="one",
    )
    first_id = _apply(
        store,
        candidate_ids=first_candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="java runtime",
        statement="项目默认使用 Java 21 runtime",
        owner="one",
    )
    first = store.read(first_id)

    second_turn, second_candidates = _extract(
        store,
        user_text="项目最终决定采用 Java 25 runtime",
        kind="project_decision",
        subject="java runtime",
        statement="项目改用 Java 25 runtime",
        owner="two",
    )
    batch = store.claim_consolidation_job("inspect-related", force=True)
    assert batch is not None
    payload = store.consolidation_payload(batch)
    assert [item["id"] for item in payload["active_items"]] == [first_id]
    second_id = store.apply_consolidation(
        batch,
        ConsolidationResult(
            operations=(
                ConsolidationOperation(
                    operation="revise",
                    candidate_ids=second_candidates,
                    target_id=first_id,
                    kind="project_decision",
                    subject="java runtime",
                    statement="项目改用 Java 25 runtime",
                    content="项目改用 Java 25 runtime",
                    strength="hard",
                    origin="explicit",
                    recall_mode="always",
                    applies_to_paths=(),
                    aliases=("java runtime",),
                    keywords=("runtime",),
                ),
            )
        ),
    )[0]
    second = store.read(second_id)

    assert first.logical_id == second.logical_id
    assert second.revision == 2
    assert second.kind == "project_decision"
    assert [source.turn_id for source in second.sources] == [second_turn]
    assert [source.turn_id for source in store.show_item(first_id).sources] == [first_turn]


def test_apply_is_not_a_memory_turn_and_temporary_rule_is_not_durable(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    with pytest.raises(ValueError, match="不支持"):
        store.begin_turn("apply", "apply", "owner")

    text = "这次不要使用三元表达式"
    turn = store.begin_turn("patch_revise", text, "turn", ["A.java"])
    store.complete_turn(turn.id, "已修订", "turn")
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None
    candidate = ExtractionCandidateInput(
        kind="user_preference",
        subject="conditional expression style",
        statement="补丁禁止三元表达式",
        content="补丁禁止三元表达式",
        strength="hard",
        origin="explicit",
        recall_mode="always",
        applies_to_paths=("A.java",),
        aliases=("三元表达式",),
        keywords=("ternary",),
        sources=(CandidateSource(turn.id, "user", text),),
    )

    assert store.complete_extraction(
        batch,
        ExtractionResult("当前补丁限制", (candidate,)),
    ) == ()


@pytest.mark.parametrize(
    ("kind", "strength", "recall_mode"),
    (
        ("project_decision", "hard", "always"),
        ("discussion_context", "soft", "on_match"),
    ),
)
def test_local_repair_clause_cannot_become_durable_memory(
    tmp_path: Path,
    kind: str,
    strength: str,
    recall_mode: str,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    user_text = "这里改为使用 StringBuilder"
    turn = store.begin_turn("patch_revise", user_text, "turn", ["A.java"])
    store.complete_turn(turn.id, "已修订", "turn")
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None
    candidate = ExtractionCandidateInput(
        kind=kind,
        subject="string construction",
        statement="项目改为使用 StringBuilder",
        content="项目改为使用 StringBuilder",
        strength=strength,
        origin="explicit",
        recall_mode=recall_mode,
        applies_to_paths=("A.java",),
        aliases=("StringBuilder",),
        keywords=("string",),
        sources=(CandidateSource(turn.id, "user", "改为使用 StringBuilder"),),
    )

    assert store.complete_extraction(
        batch,
        ExtractionResult("当前补丁限制", (candidate,)),
    ) == ()


@pytest.mark.parametrize(
    ("subject", "statement", "content"),
    (
        ("Foo.java", "当前使用 Java 17", "后续讨论背景"),
        ("runtime", "当前 Foo.java 使用 Java 17", "后续讨论背景"),
        ("runtime", "后续讨论背景", "当前 Foo.java 使用 Java 17"),
    ),
)
def test_discussion_rejects_code_fact_across_complete_semantics(
    tmp_path: Path,
    subject: str,
    statement: str,
    content: str,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    user_text = "当前 Foo.java 使用 Java 17"
    turn = store.begin_turn("general_chat", user_text, "turn")
    store.complete_turn(turn.id, "收到", "turn")
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None
    candidate = ExtractionCandidateInput(
        kind="discussion_context",
        subject=subject,
        statement=statement,
        content=content,
        strength="soft",
        origin="explicit",
        recall_mode="on_match",
        applies_to_paths=(),
        aliases=("runtime",),
        keywords=("java",),
        sources=(CandidateSource(turn.id, "user", user_text),),
    )

    assert store.complete_extraction(
        batch,
        ExtractionResult("后续讨论背景", (candidate,)),
    ) == ()


def test_discussion_candidate_must_reference_current_extraction_batch(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    old_text = "我们以后继续讨论日志格式"
    old_turn = store.begin_turn("general_chat", old_text, "old")
    store.complete_turn(old_turn.id, "收到", "old")
    old_batch = store.claim_extraction_batch("extract-old", force=True)
    assert old_batch is not None
    store.complete_extraction(old_batch, ExtractionResult("旧讨论", ()))

    current_turn = store.begin_turn("general_chat", "继续刚才的话题", "current")
    store.complete_turn(current_turn.id, "请继续", "current")
    current_batch = store.claim_extraction_batch("extract-current", force=True)
    assert current_batch is not None
    candidate = ExtractionCandidateInput(
        kind="discussion_context",
        subject="日志格式讨论",
        statement="后续继续讨论日志格式",
        content="尚未形成项目决定",
        strength="soft",
        origin="explicit",
        recall_mode="on_match",
        applies_to_paths=(),
        aliases=("日志格式",),
        keywords=("logging",),
        sources=(CandidateSource(old_turn.id, "user", old_text),),
    )

    assert store.complete_extraction(
        current_batch,
        ExtractionResult("继续讨论", (candidate,)),
    ) == ()


def test_short_confirmation_adopts_adjacent_assistant_decision(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    proposal = store.begin_turn("general_chat", "我们讨论项目运行时", "one")
    assistant_decision = "建议项目决定采用 Java 21 runtime"
    store.complete_turn(proposal.id, assistant_decision, "one")
    confirmation_text = "同意 就这么做"
    confirmation = store.begin_turn("general_chat", confirmation_text, "two")
    store.complete_turn(confirmation.id, "收到", "two")
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None
    candidate = ExtractionCandidateInput(
        kind="project_decision",
        subject="java runtime",
        statement="项目采用 Java 21 runtime",
        content="项目采用 Java 21 runtime",
        strength="hard",
        origin="adopted_proposal",
        recall_mode="always",
        applies_to_paths=(),
        aliases=("jdk",),
        keywords=("java21",),
        sources=(
            CandidateSource(proposal.id, "assistant", assistant_decision),
            CandidateSource(confirmation.id, "user", confirmation_text),
        ),
    )

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult("已确定 Java runtime", (candidate,)),
    )

    assert len(candidate_ids) == 1


def test_inferred_repetition_requires_three_turns_across_two_paths(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    sources: list[CandidateSource] = []
    for index, path in enumerate(("A.java", "A.java", "B.java"), start=1):
        text = f"这次不要使用三元表达式，修订 {index}"
        turn = store.begin_turn(
            "patch_revise",
            text,
            f"turn-{index}",
            [path],
            [f"scan-{index}:F1"],
        )
        store.complete_turn(turn.id, "已修订", f"turn-{index}")
        sources.append(CandidateSource(turn.id, "user", text))
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None
    candidate = ExtractionCandidateInput(
        kind="user_preference",
        subject="conditional expression style",
        statement="相关补丁倾向避免三元表达式",
        content="用户在多个独立文件的补丁中重复要求避免三元表达式",
        strength="soft",
        origin="inferred_repetition",
        recall_mode="on_match",
        applies_to_paths=(),
        aliases=("三元表达式",),
        keywords=("ternary",),
        sources=tuple(sources),
    )

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult("重复修订偏好", (candidate,)),
    )

    assert len(candidate_ids) == 1


def test_repetition_evidence_survives_prior_extraction_batches(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    source_rows: list[tuple[str, str]] = []
    for index, path in enumerate(("A.java", "B.java", "C.java"), start=1):
        text = f"不要使用三元表达式，修订 finding {index}"
        turn = store.begin_turn(
            "patch_revise",
            text,
            f"turn-{index}",
            [path],
            [f"scan-{index}:F1"],
        )
        store.complete_turn(turn.id, "已修订", f"turn-{index}")
        source_rows.append((turn.id, text))
        batch = store.claim_extraction_batch(f"extract-{index}", force=True)
        assert batch is not None
        if index < 3:
            store.complete_extraction(
                batch,
                ExtractionResult(f"已处理修订 {index}", ()),
            )

    payload = store.extraction_payload(batch)
    evidence_ids = tuple(
        turn["turn_id"] for turn in payload["recent_repair_evidence"]
    )
    assert evidence_ids == tuple(turn_id for turn_id, _ in source_rows)
    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            "重复补丁纠正",
            (
                ExtractionCandidateInput(
                    kind="user_preference",
                    subject="ternary expression style",
                    statement="相关补丁倾向避免三元表达式",
                    content="三个独立 finding 中都要求避免三元表达式",
                    strength="soft",
                    origin="inferred_repetition",
                    recall_mode="on_match",
                    applies_to_paths=(),
                    aliases=("三元表达式",),
                    keywords=("ternary",),
                    sources=tuple(
                        CandidateSource(turn_id, "user", text)
                        for turn_id, text in source_rows
                    ),
                ),
            ),
        ),
        evidence_turn_ids=evidence_ids,
    )

    assert len(candidate_ids) == 1


def test_repeated_correction_on_same_finding_is_not_promoted(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    sources: list[CandidateSource] = []
    for index, path in enumerate(("A.java", "B.java", "B.java"), start=1):
        text = f"不要使用三元表达式，第 {index} 次修订"
        turn = store.begin_turn(
            "patch_revise",
            text,
            f"turn-{index}",
            [path],
            ["scan-1:F1"],
        )
        store.complete_turn(turn.id, "已修订", f"turn-{index}")
        sources.append(CandidateSource(turn.id, "user", text))
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            "同一 finding 的反复修订",
            (
                ExtractionCandidateInput(
                    kind="user_preference",
                    subject="ternary expression style",
                    statement="相关补丁倾向避免三元表达式",
                    content="避免三元表达式",
                    strength="soft",
                    origin="inferred_repetition",
                    recall_mode="on_match",
                    applies_to_paths=(),
                    aliases=("三元表达式",),
                    keywords=("ternary",),
                    sources=tuple(sources),
                ),
            ),
        ),
    )

    assert candidate_ids == ()


def test_repetition_keys_must_be_distinct_per_source_turn(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    sources: list[CandidateSource] = []
    evidence_keys = (
        ["scan-1:F1", "scan-2:F2", "scan-3:F3"],
        ["scan-1:F1"],
        [],
    )
    for index, (path, keys) in enumerate(
        zip(("A.java", "A.java", "B.java"), evidence_keys),
        start=1,
    ):
        text = f"不要使用三元表达式，第 {index} 次修订"
        turn = store.begin_turn(
            "patch_revise",
            text,
            f"turn-{index}",
            [path],
            keys,
        )
        store.complete_turn(turn.id, "已修订", f"turn-{index}")
        sources.append(CandidateSource(turn.id, "user", text))
    batch = store.claim_extraction_batch("extract", force=True)
    assert batch is not None

    candidate_ids = store.complete_extraction(
        batch,
        ExtractionResult(
            "错误聚合的 finding evidence",
            (
                ExtractionCandidateInput(
                    kind="user_preference",
                    subject="ternary expression style",
                    statement="相关补丁倾向避免三元表达式",
                    content="避免三元表达式",
                    strength="soft",
                    origin="inferred_repetition",
                    recall_mode="on_match",
                    applies_to_paths=(),
                    aliases=("三元表达式",),
                    keywords=("ternary",),
                    sources=tuple(sources),
                ),
            ),
        ),
    )

    assert candidate_ids == ()


def _recall_policy(thread_id: str, *, map_budget: int = 8_192) -> RecallPolicy:
    return RecallPolicy(
        intent="general_chat",
        thread_id=thread_id,
        allowed_kinds=(
            "user_preference",
            "project_decision",
            "discussion_context",
        ),
        allow_recent_history=True,
        allow_thread_checkpoint=True,
        allow_discussion=True,
        durable_token_budget=24_576,
        map_token_budget=map_budget,
    )


def test_memory_map_has_standing_and_query_gated_relevant_lanes(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    _, standing_candidates = _extract(
        store,
        user_text="以后默认使用 Java 21 runtime",
        kind="user_preference",
        subject="java runtime",
        statement="项目默认使用 Java 21 runtime",
        owner="standing",
    )
    standing_id = _apply(
        store,
        candidate_ids=standing_candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="java runtime",
        statement="项目默认使用 Java 21 runtime",
        owner="standing",
    )
    _, relevant_candidates = _extract(
        store,
        user_text="以后涉及空值校验时偏好 Objects.requireNonNull",
        kind="user_preference",
        subject="null guard style",
        statement="相关空值校验优先考虑 Objects.requireNonNull",
        owner="relevant",
        strength="soft",
        recall_mode="on_match",
        aliases=("Objects.requireNonNull",),
        keywords=("null guard",),
    )
    relevant_id = _apply(
        store,
        candidate_ids=relevant_candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="null guard style",
        statement="相关空值校验优先考虑 Objects.requireNonNull",
        owner="relevant",
        strength="soft",
        recall_mode="on_match",
        aliases=("Objects.requireNonNull",),
        keywords=("null guard",),
    )
    thread_id = store.ensure_active_thread().id
    policy = _recall_policy(thread_id)

    memory_map = manager.build_memory_map(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="请解释 require_non_null 的选择",
        ),
        policy,
    )
    unrelated_map = manager.build_memory_map(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="解释线程池",
        ),
        policy,
    )
    tiny_map = manager.build_memory_map(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="require_non_null",
        ),
        _recall_policy(thread_id, map_budget=1),
    )

    assert [(entry.id, entry.lane) for entry in memory_map.entries] == [
        (standing_id, "standing"),
        (relevant_id, "relevant"),
    ]
    assert [entry.id for entry in unrelated_map.entries] == [standing_id]
    assert tiny_map.entries == ()
    assert tiny_map.omitted_count == 2


def test_recall_requires_path_eligibility_and_two_content_terms(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    path = "src/main/java/demo/UserService.java"
    _, candidates = _extract(
        store,
        user_text="以后在 UserService.java 默认偏好 guard clause",
        kind="user_preference",
        subject="service control flow",
        statement="UserService 优先使用 guard clause",
        content="null guard service control flow",
        owner="path",
        strength="soft",
        recall_mode="on_match",
        paths=(path,),
        aliases=("service control flow",),
        keywords=("guard clause",),
    )
    item_id = _apply(
        store,
        candidate_ids=candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="service control flow",
        statement="UserService 优先使用 guard clause",
        content="null guard service control flow",
        owner="path",
        strength="soft",
        recall_mode="on_match",
        paths=(path,),
        aliases=("service control flow",),
        keywords=("guard clause",),
    )
    thread_id = store.ensure_active_thread().id
    policy = _recall_policy(thread_id)

    one_term = manager.search_recall(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="null",
            paths=(path,),
        ),
        policy,
    )
    two_terms = manager.search_recall(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="null service",
            paths=(path,),
        ),
        policy,
    )
    wrong_path = manager.search_recall(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="null service",
            paths=("src/main/java/demo/OrderService.java",),
        ),
        policy,
    )

    assert one_term == []
    assert [hit.id for hit in two_terms] == [item_id]
    assert wrong_path == []


@pytest.mark.parametrize(
    ("content", "keywords", "expected_match_type"),
    (
        ("structured event payload", ("日志格式",), "alias_or_keyword"),
        ("日志格式统一处理", ("logging",), "content_terms"),
    ),
)
def test_recall_matches_unspaced_chinese_query(
    tmp_path: Path,
    content: str,
    keywords: tuple[str, ...],
    expected_match_type: str,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    _, candidates = _extract(
        store,
        user_text="以后讨论日志输出时偏好遵循项目格式",
        kind="user_preference",
        subject="logging convention",
        statement="日志输出遵循项目格式",
        content=content,
        owner="chinese-recall",
        strength="soft",
        recall_mode="on_match",
        aliases=("logging convention",),
        keywords=keywords,
    )
    item_id = _apply(
        store,
        candidate_ids=candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="logging convention",
        statement="日志输出遵循项目格式",
        content=content,
        owner="chinese-recall",
        strength="soft",
        recall_mode="on_match",
        aliases=("logging convention",),
        keywords=keywords,
    )
    thread_id = store.ensure_active_thread().id
    policy = _recall_policy(thread_id)

    hits = manager.search_recall(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="日志格式怎么处理",
        ),
        policy,
    )
    unrelated = manager.search_recall(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="数据库连接怎么处理",
        ),
        policy,
    )

    assert [(hit.id, hit.match_type) for hit in hits] == [
        (item_id, expected_match_type)
    ]
    assert unrelated == []


def test_request_policy_enforces_readable_ids_call_limits_and_shared_pool(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    _, standing_candidates = _extract(
        store,
        user_text="以后默认偏好简洁回答",
        kind="user_preference",
        subject="response style",
        statement="回答默认保持简洁",
        owner="standing-request",
    )
    standing_id = _apply(
        store,
        candidate_ids=standing_candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="response style",
        statement="回答默认保持简洁",
        owner="standing-request",
    )
    _, relevant_candidates = _extract(
        store,
        user_text="以后涉及测试时偏好 focused pytest",
        kind="user_preference",
        subject="test execution",
        statement="相关验证优先运行 focused pytest",
        owner="relevant-request",
        strength="soft",
        recall_mode="on_match",
        aliases=("focused pytest",),
        keywords=("test execution",),
    )
    relevant_id = _apply(
        store,
        candidate_ids=relevant_candidates,
        operation="create",
        target_id=None,
        kind="user_preference",
        subject="test execution",
        statement="相关验证优先运行 focused pytest",
        owner="relevant-request",
        strength="soft",
        recall_mode="on_match",
        aliases=("focused pytest",),
        keywords=("test execution",),
    )
    thread_id = store.ensure_active_thread().id
    policy = _recall_policy(thread_id)
    state = manager.open_memory_request(
        RecallQuery(
            intent="general_chat",
            thread_id=thread_id,
            user_text="解释构建",
        ),
        policy,
    )

    assert standing_id in state.readable_ids
    with pytest.raises(MemoryNotFoundError, match="未由本请求"):
        manager.read_memory_request(state, relevant_id)
    before_search = state.remaining_tokens
    hits = manager.search_memory_request(state, "focused_pytest test execution")
    assert [hit.id for hit in hits] == [relevant_id]
    assert state.remaining_tokens < before_search
    assert manager.read_memory_request(state, relevant_id).id == relevant_id
    remaining_after_read = state.remaining_tokens
    assert manager.read_memory_request(state, relevant_id).id == relevant_id
    assert state.remaining_tokens == remaining_after_read

    for query in ("one", "two", "three"):
        manager.search_memory_request(state, query)
    with pytest.raises(MemoryContractError, match="额度"):
        manager.search_memory_request(state, "fifth")
    assert manager.search_memory_request(state, "one") == []


def test_repair_policy_excludes_discussion_even_if_id_is_guessed(tmp_path: Path) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    store = manager.store
    _, candidates = _extract(
        store,
        user_text="我们正在讨论 null handling 的备选方案",
        kind="discussion_context",
        subject="null handling discussion",
        statement="当前正在比较 null handling 方案",
        owner="discussion",
        strength="soft",
        recall_mode="on_match",
        aliases=("null handling",),
        keywords=("discussion",),
    )
    discussion_id = _apply(
        store,
        candidate_ids=candidates,
        operation="create",
        target_id=None,
        kind="discussion_context",
        subject="null handling discussion",
        statement="当前正在比较 null handling 方案",
        owner="discussion",
        strength="soft",
        recall_mode="on_match",
        aliases=("null handling",),
        keywords=("discussion",),
    )
    thread_id = store.ensure_active_thread().id
    policy = manager.build_recall_policy(
        intent="code_audit",
        thread_id=thread_id,
        durable_token_budget=24_576,
        map_token_budget=8_192,
    )
    state = manager.open_memory_request(
        RecallQuery(
            intent="code_audit",
            thread_id=thread_id,
            user_text="审查 null handling",
            finding_path="src/main/java/demo/A.java",
        ),
        policy,
    )

    assert discussion_id not in state.readable_ids
    assert manager.search_memory_request(state, "null handling") == []
    state.readable_ids.add(discussion_id)
    with pytest.raises(MemoryNotFoundError, match="policy"):
        manager.read_memory_request(state, discussion_id)
