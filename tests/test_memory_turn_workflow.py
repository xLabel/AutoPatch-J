from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from autopatch_j.agent.session import AgentSession
from autopatch_j.cli.workflows.memory_turn import run_durable_memory_turn
from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory import (
    MemoryContractError,
    MemoryLeaseError,
    MemoryManager,
    MemoryNotFoundError,
    MemoryStorageError,
    MemoryThreadConflictError,
)


def test_repair_turn_persists_only_raw_user_and_final_assistant_text(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    session = MagicMock()

    result = run_durable_memory_turn(
        manager=manager,
        session=session,
        intent=IntentType.PATCH_REVISE,
        user_text="不要使用三元表达式",
        scope_paths=["src/main/java/demo/A.java"],
        evidence_keys=["F-1"],
        run=lambda: SimpleNamespace(
            display_answer="已按要求修订补丁",
            internal_trace="tool and reasoning must not persist",
        ),
        assistant_text=lambda value: value.display_answer,
        on_degraded=lambda _: None,
    )

    exported = manager.export(tmp_path / "exports")
    payload = json.loads(exported.path.read_text(encoding="utf-8"))
    turn = payload["turns"][0]
    assert result.display_answer == "已按要求修订补丁"
    assert turn["intent"] == "patch_revise"
    assert turn["user_text"] == "不要使用三元表达式"
    assert turn["assistant_text"] == "已按要求修订补丁"
    assert json.loads(turn["scope_paths_json"]) == ["src/main/java/demo/A.java"]
    assert json.loads(turn["evidence_keys_json"]) == ["F-1"]
    assert "internal_trace" not in json.dumps(payload, ensure_ascii=False)
    session.bind_memory_thread.assert_called_once()
    session.clear_memory_thread.assert_called_once_with()


def test_primary_failure_marks_turn_failed_without_hiding_original_error(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(db_path=tmp_path / "memory.db")
    session = MagicMock()

    with pytest.raises(RuntimeError, match="provider unavailable"):
        run_durable_memory_turn(
            manager=manager,
            session=session,
            intent=IntentType.CODE_AUDIT,
            user_text="审查 A.java",
            scope_paths=["A.java"],
            evidence_keys=[],
            run=lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
            assistant_text=lambda _: "unreachable",
            on_degraded=lambda _: None,
        )

    exported = manager.export(tmp_path / "exports")
    payload = json.loads(exported.path.read_text(encoding="utf-8"))
    assert payload["turns"][0]["state"] == "failed"
    assert payload["turns"][0]["assistant_text"] == ""
    session.clear_memory_thread.assert_called_once_with()


def test_degraded_memory_warns_once_and_does_not_block_primary_work(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    db_path.write_bytes(b"not sqlite")
    manager = MemoryManager(db_path=db_path)
    session = MagicMock()
    notices: list[str] = []

    for value in ("first", "second"):
        result = run_durable_memory_turn(
            manager=manager,
            session=session,
            intent=IntentType.GENERAL_CHAT,
            user_text=value,
            scope_paths=[],
            evidence_keys=[],
            run=lambda value=value: value,
            assistant_text=str,
            on_degraded=notices.append,
        )
        assert result == value

    assert len(notices) == 1
    assert "Memory degraded" in notices[0]
    session.bind_memory_thread.assert_not_called()


def test_degraded_memory_skips_ordinary_history_and_runs_primary_work(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.db"
    db_path.write_bytes(b"not sqlite")
    manager = MemoryManager(db_path=db_path)
    session = AgentSession(
        repo_root=tmp_path,
        artifact_manager=MagicMock(),
        workspace_manager=MagicMock(),
        symbol_indexer=MagicMock(),
        patch_engine=MagicMock(),
        code_fetcher=MagicMock(),
        memory_manager=manager,
    )
    notices: list[str] = []

    result = run_durable_memory_turn(
        manager=manager,
        session=session,
        intent=IntentType.GENERAL_CHAT,
        user_text="继续普通对话",
        scope_paths=[],
        evidence_keys=[],
        run=lambda: session.build_thread_history(
            IntentType.GENERAL_CHAT,
            max_tokens=1_000,
        ),
        assistant_text=str,
        on_degraded=notices.append,
    )

    assert result == []
    assert len(notices) == 1
    assert "Memory degraded" in notices[0]


@pytest.mark.parametrize(
    "error",
    (
        MemoryStorageError("storage unavailable"),
        MemoryLeaseError("turn lease expired"),
        MemoryNotFoundError("turn removed"),
        MemoryThreadConflictError("thread switched"),
    ),
)
def test_turn_completion_memory_errors_do_not_hide_successful_work(
    error: Exception,
) -> None:
    manager = MagicMock(spec=MemoryManager)
    manager.begin_turn.return_value = SimpleNamespace(id="turn-1", thread_id="thread-1")
    manager.complete_turn.side_effect = error
    manager.degraded_notice.return_value = "Memory degraded: unavailable"
    notices: list[str] = []

    result = run_durable_memory_turn(
        manager=manager,
        session=MagicMock(),
        intent=IntentType.CODE_AUDIT,
        user_text="审查 A.java",
        scope_paths=["A.java"],
        evidence_keys=[],
        run=lambda: "business-ok",
        assistant_text=str,
        on_degraded=notices.append,
    )

    assert result == "business-ok"
    assert notices == ["Memory degraded: unavailable"]


def test_turn_failure_memory_error_does_not_hide_primary_error() -> None:
    manager = MagicMock(spec=MemoryManager)
    manager.begin_turn.return_value = SimpleNamespace(id="turn-1", thread_id="thread-1")
    manager.fail_turn.side_effect = MemoryLeaseError("turn lease expired")
    manager.degraded_notice.return_value = "Memory degraded: unavailable"

    with pytest.raises(RuntimeError, match="provider unavailable"):
        run_durable_memory_turn(
            manager=manager,
            session=MagicMock(),
            intent=IntentType.CODE_AUDIT,
            user_text="审查 A.java",
            scope_paths=["A.java"],
            evidence_keys=[],
            run=lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
            assistant_text=str,
            on_degraded=lambda _: None,
        )


def test_memory_contract_error_during_completion_remains_explicit() -> None:
    manager = MagicMock(spec=MemoryManager)
    manager.begin_turn.return_value = SimpleNamespace(id="turn-1", thread_id="thread-1")
    manager.complete_turn.side_effect = MemoryContractError("invalid turn contract")

    with pytest.raises(MemoryContractError, match="invalid turn contract"):
        run_durable_memory_turn(
            manager=manager,
            session=MagicMock(),
            intent=IntentType.CODE_AUDIT,
            user_text="审查 A.java",
            scope_paths=["A.java"],
            evidence_keys=[],
            run=lambda: "business-ok",
            assistant_text=str,
            on_degraded=lambda _: None,
        )

    manager.degraded_notice.assert_not_called()
