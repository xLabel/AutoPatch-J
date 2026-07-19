from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from autopatch_j.agent.session import AgentSession
from autopatch_j.core.domain import IntentType
from autopatch_j.core.memory import MemoryContractError, MemoryError, MemoryManager
from autopatch_j.core.memory.errors import MemoryStorageError


ResultT = TypeVar("ResultT")


def run_durable_memory_turn(
    *,
    manager: MemoryManager,
    session: AgentSession,
    intent: IntentType,
    user_text: str,
    scope_paths: Sequence[str],
    evidence_keys: Sequence[str],
    run: Callable[[], ResultT],
    assistant_text: Callable[[ResultT], str],
    on_degraded: Callable[[str], None],
) -> ResultT:
    """先持久化用户 turn；Memory 故障不得阻断主业务。"""

    try:
        turn = manager.begin_turn(
            intent=intent,
            user_text=user_text,
            scope_paths=list(scope_paths),
            evidence_keys=list(evidence_keys),
        )
    except MemoryStorageError as exc:
        notice = manager.degraded_notice(exc)
        if notice:
            on_degraded(notice)
        return run()

    session.bind_memory_thread(turn.thread_id)
    try:
        try:
            result = run()
        except BaseException as exc:
            try:
                manager.fail_turn(
                    turn.id,
                    error=f"{type(exc).__name__}: {exc}".strip(),
                )
            except MemoryContractError:
                raise
            except MemoryError as memory_exc:
                notice = manager.degraded_notice(memory_exc)
                if notice:
                    on_degraded(notice)
            raise
        try:
            manager.complete_turn(
                turn.id,
                assistant_text=assistant_text(result),
            )
        except MemoryContractError:
            raise
        except MemoryError as exc:
            notice = manager.degraded_notice(exc)
            if notice:
                on_degraded(notice)
        return result
    finally:
        session.clear_memory_thread()
