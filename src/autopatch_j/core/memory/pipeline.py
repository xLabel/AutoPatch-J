from __future__ import annotations

import json
import sqlite3
import threading
from copy import deepcopy
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from autopatch_j.llm.context_window import (
    ContextCapacityError,
    ModelContextProfile,
    clip_text_head_tail,
    estimate_request_tokens,
    estimate_text_tokens,
)
from autopatch_j.llm.options import LLMCallPurpose
from autopatch_j.llm.errors import (
    LLMProtocolError,
    LLMResponseError,
    LLMTransientError,
    LLMTruncationError,
)

from .contracts import parse_consolidation_response, parse_extraction_response
from .errors import (
    MemoryContractViolation,
    MemoryCorruptError,
    MemoryLeaseError,
    MemoryOutputContractError,
    MemorySchemaError,
)
from .models import (
    ClaimedJobBatch,
    ConsolidationResult,
    ExtractionResult,
    ExtractionSourceInput,
    JobKind,
    MemoryFailureDiagnostic,
)
from .prompts import (
    MEMORY_CONSOLIDATION_REPAIR_SYSTEM_PROMPT,
    MEMORY_CONSOLIDATION_SYSTEM_PROMPT,
    MEMORY_EXTRACTION_REPAIR_SYSTEM_PROMPT,
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
)
from .store import MemoryStore


class MemoryLLM(Protocol):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: LLMCallPurpose = LLMCallPurpose.REACT,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class PipelineStepResult:
    processed: int
    succeeded: int
    failed: int
    error: str = ""
    processed_job_ids: tuple[str, ...] = ()
    job_kind: str = ""
    spawned_job_ids: tuple[str, ...] = ()


class _MemoryRepairFailure(RuntimeError):
    def __init__(
        self,
        batch: ClaimedJobBatch,
        diagnostic: MemoryFailureDiagnostic,
    ) -> None:
        self.batch = batch
        self.diagnostic = diagnostic
        super().__init__("Memory repair failed")


class _ProviderLeaseHeartbeat:
    def __init__(
        self,
        store: MemoryStore,
        batch: ClaimedJobBatch,
        owner: str,
        interval_seconds: float,
    ) -> None:
        self.store = store
        self.batch = batch
        self.owner = owner
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lost_error: Exception | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="autopatch-memory-provider-heartbeat",
            daemon=True,
        )

    def __enter__(self) -> _ProviderLeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stop.set()
        self._thread.join()
        if self._lost_error is not None:
            raise self._lost_error

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.batch = self.store.renew_job_lease(self.batch)
                self.store.heartbeat_open_turns(self.owner)
            except (MemoryLeaseError, MemorySchemaError, MemoryCorruptError) as exc:
                self._lost_error = exc
                self._stop.set()
            except Exception:
                # Short storage contention is retried by the next heartbeat; the
                # post-call Store operation remains the authoritative fence.
                continue


class MemoryPipeline:
    def __init__(
        self,
        store: MemoryStore,
        llm: MemoryLLM,
        owner: str,
        *,
        heartbeat_interval_seconds: float = 30.0,
    ) -> None:
        self.store = store
        self.llm = llm
        self.owner = owner
        self.heartbeat_interval_seconds = heartbeat_interval_seconds

    def _provider_heartbeat(
        self,
        batch: ClaimedJobBatch,
    ) -> _ProviderLeaseHeartbeat:
        return _ProviderLeaseHeartbeat(
            self.store,
            batch,
            self.owner,
            self.heartbeat_interval_seconds,
        )

    def process_one(
        self,
        *,
        force: bool = False,
        thread_id: str | None = None,
        allowed_job_ids: Collection[str] | None = None,
    ) -> PipelineStepResult | None:
        batch = self.store.claim_extraction_batch(
            self.owner,
            force=force,
            thread_id=thread_id,
            allowed_job_ids=allowed_job_ids,
        )
        if batch is not None:
            return self._process_extraction(batch)
        batch = self.store.claim_consolidation_job(
            self.owner,
            force=force,
            thread_id=thread_id,
            allowed_job_ids=allowed_job_ids,
        )
        if batch is not None:
            return self._process_consolidation(batch)
        return None

    def _process_extraction(self, batch: ClaimedJobBatch) -> PipelineStepResult:
        try:
            payload = self._fit_extraction_payload(
                self.store.extraction_payload(batch)
            )
            source_inputs = self._extraction_source_inputs(payload)
            batch = self.store.reserve_llm_call(batch)
            try:
                with self._provider_heartbeat(batch):
                    response = self.llm.chat(
                        messages=[
                            {"role": "system", "content": MEMORY_EXTRACTION_SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": json.dumps(payload, ensure_ascii=False),
                            },
                        ],
                        tools=None,
                        purpose=LLMCallPurpose.MEMORY_EXTRACTION,
                    )
            except LLMTruncationError:
                violation_error = MemoryOutputContractError(
                    MemoryContractViolation(
                        path="response",
                        rule="finish_reason_length",
                        observed_type="truncated",
                    )
                )
                batch, payload, source_inputs, response, result = self._repair_extraction(
                    batch,
                    payload,
                    violation_error,
                    None,
                )
            else:
                try:
                    result = parse_extraction_response(response.content)
                    self._validate_extraction_result(result, payload, source_inputs)
                except MemoryOutputContractError as exc:
                    batch, payload, source_inputs, response, result = (
                        self._repair_extraction(
                            batch,
                            payload,
                            exc,
                            response,
                        )
                    )
            candidate_ids = self.store.complete_extraction(
                batch,
                result,
                source_inputs=source_inputs,
            )
            spawned_job_ids = self.store.consolidation_job_ids_for_candidates(
                candidate_ids
            )
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=len(batch.jobs),
                failed=0,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.EXTRACTION.value,
                spawned_job_ids=spawned_job_ids,
            )
        except _MemoryRepairFailure as exc:
            return self._complete_repair_failure(exc.batch, exc.diagnostic)
        except ContextCapacityError as exc:
            return self._complete_capacity_skip(batch, "extraction", exc)
        except Exception as exc:
            return self._handle_primary_failure(batch, "extraction", exc)

    def _process_consolidation(self, batch: ClaimedJobBatch) -> PipelineStepResult:
        try:
            payload = self._fit_consolidation_payload(
                self.store.consolidation_payload(batch)
            )
            batch = self.store.reserve_llm_call(batch)
            try:
                with self._provider_heartbeat(batch):
                    response = self.llm.chat(
                        messages=[
                            {"role": "system", "content": MEMORY_CONSOLIDATION_SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": json.dumps(payload, ensure_ascii=False),
                            },
                        ],
                        tools=None,
                        purpose=LLMCallPurpose.MEMORY_CONSOLIDATION,
                    )
            except LLMTruncationError:
                violation_error = MemoryOutputContractError(
                    MemoryContractViolation(
                        path="response",
                        rule="finish_reason_length",
                        observed_type="truncated",
                    )
                )
                batch, response, result = self._repair_consolidation(
                    batch,
                    payload,
                    violation_error,
                    None,
                )
            else:
                try:
                    result = parse_consolidation_response(response.content)
                    self._validate_consolidation_result(result, payload)
                except MemoryOutputContractError as exc:
                    batch, response, result = self._repair_consolidation(
                        batch,
                        payload,
                        exc,
                        response,
                    )
            self.store.apply_consolidation(batch, result)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=len(batch.jobs),
                failed=0,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.CONSOLIDATION.value,
            )
        except _MemoryRepairFailure as exc:
            return self._complete_repair_failure(exc.batch, exc.diagnostic)
        except ContextCapacityError as exc:
            return self._complete_capacity_skip(batch, "consolidation", exc)
        except Exception as exc:
            return self._handle_primary_failure(batch, "consolidation", exc)

    def _record_failure(
        self,
        batch: ClaimedJobBatch,
        diagnostic: MemoryFailureDiagnostic,
        *,
        terminal: bool,
    ) -> None:
        try:
            serialized = self._serialize_diagnostic(diagnostic)
            if terminal:
                self.store.fail_job(batch, serialized)
            else:
                self.store.defer_job_retry(batch, serialized)
        except Exception:
            # clear/lease fencing errors are already the authoritative result; a stale
            # worker must never turn them into a second write.
            return

    def _handle_primary_failure(
        self,
        batch: ClaimedJobBatch,
        phase: str,
        exc: Exception,
    ) -> PipelineStepResult:
        transient = self._is_transient_primary_error(exc)
        terminal = not transient or any(
            job.attempt_count >= 2 for job in batch.jobs
        )
        diagnostic = self._failure_diagnostic(
            batch,
            phase,
            "primary",
            exc,
        )
        self._record_failure(batch, diagnostic, terminal=terminal)
        return PipelineStepResult(
            processed=len(batch.jobs),
            succeeded=0,
            failed=len(batch.jobs) if terminal else 0,
            error=self._serialize_diagnostic(diagnostic),
            processed_job_ids=tuple(job.id for job in batch.jobs),
            job_kind=batch.jobs[0].kind,
        )

    def _repair_extraction(
        self,
        batch: ClaimedJobBatch,
        payload: dict[str, Any],
        violation_error: MemoryOutputContractError,
        primary_response: Any,
    ) -> tuple[
        ClaimedJobBatch,
        dict[str, Any],
        tuple[ExtractionSourceInput, ...],
        Any,
        ExtractionResult,
    ]:
        del primary_response
        response: Any = None
        try:
            batch = self.store.renew_job_lease(batch)
            violation = self._violation_payload(violation_error.violation)
            fitted_payload = self._fit_extraction_payload(
                payload,
                system_prompt=MEMORY_EXTRACTION_REPAIR_SYSTEM_PROMPT,
                violation=violation,
            )
            source_inputs = self._extraction_source_inputs(fitted_payload)
            batch = self.store.reserve_llm_call(batch)
            with self._provider_heartbeat(batch):
                response = self.llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": MEMORY_EXTRACTION_REPAIR_SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "business_input": fitted_payload,
                                    "violation": violation,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    tools=None,
                    purpose=LLMCallPurpose.MEMORY_EXTRACTION_REPAIR,
                )
            result = parse_extraction_response(response.content)
            self._validate_extraction_result(
                result,
                fitted_payload,
                source_inputs,
            )
            return batch, fitted_payload, source_inputs, response, result
        except Exception as exc:
            raise _MemoryRepairFailure(
                batch,
                self._failure_diagnostic(
                    batch,
                    "extraction",
                    "repair",
                    exc,
                    response=response,
                ),
            ) from None

    def _repair_consolidation(
        self,
        batch: ClaimedJobBatch,
        payload: dict[str, Any],
        violation_error: MemoryOutputContractError,
        primary_response: Any,
    ) -> tuple[ClaimedJobBatch, Any, ConsolidationResult]:
        del primary_response
        response: Any = None
        try:
            batch = self.store.renew_job_lease(batch)
            violation = self._violation_payload(violation_error.violation)
            fitted_payload = self._fit_consolidation_payload(
                payload,
                system_prompt=MEMORY_CONSOLIDATION_REPAIR_SYSTEM_PROMPT,
                violation=violation,
            )
            batch = self.store.reserve_llm_call(batch)
            with self._provider_heartbeat(batch):
                response = self.llm.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": MEMORY_CONSOLIDATION_REPAIR_SYSTEM_PROMPT,
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "business_input": fitted_payload,
                                    "violation": violation,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    tools=None,
                    purpose=LLMCallPurpose.MEMORY_CONSOLIDATION_REPAIR,
                )
            result = parse_consolidation_response(response.content)
            self._validate_consolidation_result(result, fitted_payload)
            return batch, response, result
        except Exception as exc:
            raise _MemoryRepairFailure(
                batch,
                self._failure_diagnostic(
                    batch,
                    "consolidation",
                    "repair",
                    exc,
                    response=response,
                ),
            ) from None

    def _complete_repair_failure(
        self,
        batch: ClaimedJobBatch,
        diagnostic: MemoryFailureDiagnostic,
    ) -> PipelineStepResult:
        self._record_failure(batch, diagnostic, terminal=True)
        return PipelineStepResult(
            processed=len(batch.jobs),
            succeeded=0,
            failed=len(batch.jobs),
            error=self._serialize_diagnostic(diagnostic),
            processed_job_ids=tuple(job.id for job in batch.jobs),
            job_kind=batch.jobs[0].kind,
        )

    def _complete_capacity_skip(
        self,
        batch: ClaimedJobBatch,
        phase: str,
        exc: ContextCapacityError,
    ) -> PipelineStepResult:
        summary = f"Memory {phase} skipped: {exc}"
        self.store.complete_job_without_output(batch, summary)
        return PipelineStepResult(
            processed=len(batch.jobs),
            succeeded=len(batch.jobs),
            failed=0,
            error=summary,
            processed_job_ids=tuple(job.id for job in batch.jobs),
            job_kind=batch.jobs[0].kind,
        )

    def _fit_extraction_payload(
        self,
        payload: dict[str, Any],
        *,
        system_prompt: str = MEMORY_EXTRACTION_SYSTEM_PROMPT,
        violation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = getattr(self.llm, "context_profile", None)
        if not isinstance(profile, ModelContextProfile):
            return payload
        projected = deepcopy(payload)

        def request_payload() -> dict[str, Any]:
            if violation is None:
                return projected
            return {
                "business_input": projected,
                "violation": violation,
            }

        if self._payload_fits(system_prompt, request_payload(), profile.input_capacity):
            return projected
        evidence = projected.get("recent_repair_evidence")
        while isinstance(evidence, list) and evidence:
            evidence.pop(0)
            if self._payload_fits(
                system_prompt,
                request_payload(),
                profile.input_capacity,
            ):
                return projected
        if projected.get("adjacent_previous_turn") is not None:
            projected["adjacent_previous_turn"] = None
            if self._payload_fits(
                system_prompt,
                request_payload(),
                profile.input_capacity,
            ):
                return projected
        if projected.get("previous_compaction"):
            projected["previous_compaction"] = ""
            if self._payload_fits(
                system_prompt,
                request_payload(),
                profile.input_capacity,
            ):
                return projected
        for field in ("assistant", "user"):
            if self._clip_largest_turn_fields(
                projected,
                field=field,
                system_prompt=system_prompt,
                input_capacity=profile.input_capacity,
                violation=violation,
            ):
                return projected
        raise ContextCapacityError(
            "Memory extraction 固定 contract 与 primary turn 投影仍超过 input capacity。"
        )

    def _fit_consolidation_payload(
        self,
        payload: dict[str, Any],
        *,
        system_prompt: str = MEMORY_CONSOLIDATION_SYSTEM_PROMPT,
        violation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        profile = getattr(self.llm, "context_profile", None)
        if not isinstance(profile, ModelContextProfile):
            return payload
        projected = deepcopy(payload)
        request_payload = (
            projected
            if violation is None
            else {
                "business_input": projected,
                "violation": violation,
            }
        )
        if not self._payload_fits(
            system_prompt,
            request_payload,
            profile.input_capacity,
        ):
            raise ContextCapacityError(
                "Memory consolidation candidates 与 related active registry "
                "超过 input capacity。"
            )
        return projected

    @staticmethod
    def _extraction_source_inputs(
        payload: dict[str, Any],
    ) -> tuple[ExtractionSourceInput, ...]:
        sources: list[ExtractionSourceInput] = []

        def append_turn(turn: Any) -> None:
            if not isinstance(turn, dict):
                return
            turn_id = str(turn.get("turn_id", ""))
            if not turn_id:
                return
            for role in ("user", "assistant"):
                sources.append(
                    ExtractionSourceInput(
                        turn_id=turn_id,
                        role=role,
                        text=str(turn.get(role, "")),
                    )
                )

        for turn in payload.get("turns", ()):
            append_turn(turn)
        append_turn(payload.get("adjacent_previous_turn"))
        for turn in payload.get("recent_repair_evidence", ()):
            append_turn(turn)
        return tuple(sources)

    def _clip_largest_turn_fields(
        self,
        payload: dict[str, Any],
        *,
        field: str,
        system_prompt: str,
        input_capacity: int,
        violation: dict[str, Any] | None = None,
    ) -> bool:
        turns = payload.get("turns")
        if not isinstance(turns, list):
            return False
        def request_payload() -> dict[str, Any]:
            if violation is None:
                return payload
            return {
                "business_input": payload,
                "violation": violation,
            }

        while not self._payload_fits(
            system_prompt,
            request_payload(),
            input_capacity,
        ):
            candidates = [
                turn
                for turn in turns
                if isinstance(turn, dict)
                and estimate_text_tokens(str(turn.get(field, ""))) > 1
            ]
            if not candidates:
                return False
            target = max(
                candidates,
                key=lambda turn: estimate_text_tokens(str(turn.get(field, ""))),
            )
            text = str(target.get(field, ""))
            current_tokens = estimate_text_tokens(text)
            overage = max(
                1,
                self._payload_tokens(system_prompt, request_payload())
                - input_capacity,
            )
            next_budget = max(1, current_tokens - overage - 8)
            clipped = clip_text_head_tail(text, next_budget)
            if clipped == text:
                clipped = clip_text_head_tail(text, max(1, current_tokens // 2))
            target[field] = clipped
        return True

    @staticmethod
    def _payload_fits(
        system_prompt: str,
        payload: dict[str, Any],
        input_capacity: int,
    ) -> bool:
        return (
            MemoryPipeline._payload_tokens(system_prompt, payload)
            <= input_capacity
        )

    @staticmethod
    def _payload_tokens(system_prompt: str, payload: dict[str, Any]) -> int:
        return estimate_request_tokens(
            [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ]
        )

    @staticmethod
    def _validate_extraction_result(
        result: ExtractionResult,
        payload: dict[str, Any],
        source_inputs: tuple[ExtractionSourceInput, ...],
    ) -> None:
        source_text = {
            (source.turn_id, source.role): source.text
            for source in source_inputs
        }
        allowed_paths: set[str] = set()
        turns: list[Any] = list(payload.get("turns", ()))
        turns.append(payload.get("adjacent_previous_turn"))
        turns.extend(payload.get("recent_repair_evidence", ()))
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            allowed_paths.update(
                str(path)
                for path in turn.get("scope_paths", ())
                if isinstance(path, str)
            )
        for candidate_index, candidate in enumerate(result.candidates):
            if not set(candidate.applies_to_paths) <= allowed_paths:
                MemoryPipeline._raise_semantic_output_error(
                    f"candidate[{candidate_index}].applies_to_paths",
                    "paths_visible_in_payload",
                    candidate.applies_to_paths,
                )
            for source_index, source in enumerate(candidate.sources):
                visible_text = source_text.get((source.turn_id, source.role))
                if visible_text is None or source.quote not in visible_text:
                    MemoryPipeline._raise_semantic_output_error(
                        f"candidate[{candidate_index}].sources[{source_index}]",
                        "exact_visible_source_quote",
                        source.quote,
                    )

    @staticmethod
    def _validate_consolidation_result(
        result: ConsolidationResult,
        payload: dict[str, Any],
    ) -> None:
        candidates = {
            str(item.get("id", "")): item
            for item in payload.get("candidates", ())
            if isinstance(item, dict) and item.get("id")
        }
        active_items = {
            str(item.get("id", "")): item
            for item in payload.get("active_items", ())
            if isinstance(item, dict) and item.get("id")
        }
        expected_ids = set(candidates)
        active_ids = set(active_items)
        handled: set[str] = set()
        for index, operation in enumerate(result.operations):
            operation_ids = set(operation.candidate_ids)
            if not operation_ids <= expected_ids or handled & operation_ids:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}].candidate_ids",
                    "visible_candidates_exactly_once",
                    operation.candidate_ids,
                )
            handled.update(operation_ids)
            if operation.operation in {"create", "reject"}:
                if operation.target_id is not None:
                    MemoryPipeline._raise_semantic_output_error(
                        f"operation[{index}].target_id",
                        "null_for_create_or_reject",
                        operation.target_id,
                    )
            elif not operation.target_id or operation.target_id not in active_ids:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}].target_id",
                    "visible_active_target",
                    operation.target_id,
                )
            if operation.operation == "reject":
                continue

            selected = [candidates[candidate_id] for candidate_id in operation.candidate_ids]
            path_sets = {
                tuple(sorted(set(candidate.get("applies_to_paths", ()))))
                for candidate in selected
            }
            if len(path_sets) != 1:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}].candidate_ids",
                    "uniform_candidate_applicability",
                    operation.candidate_ids,
                )
            expected_paths = next(iter(path_sets))
            kinds = {str(candidate.get("kind", "")) for candidate in selected}
            discussion_scope = "discussion_context" in kinds
            if discussion_scope and kinds != {"discussion_context"}:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}].candidate_ids",
                    "discussion_candidates_only",
                    operation.candidate_ids,
                )
            expected_kinds = (
                {"discussion_context"}
                if discussion_scope
                else {"user_preference", "project_decision"}
            )
            if operation.kind not in expected_kinds:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}].kind",
                    (
                        "discussion_kind_required"
                        if discussion_scope
                        else "project_kind_required"
                    ),
                    operation.kind,
                )
            if tuple(sorted(set(operation.applies_to_paths))) != expected_paths:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}].applies_to_paths",
                    "match_candidate_applicability",
                    operation.applies_to_paths,
                )

            origin_score = {
                "inferred_repetition": 0,
                "adopted_proposal": 1,
                "explicit": 2,
            }
            dominant = max(
                selected,
                key=lambda candidate: (
                    origin_score.get(str(candidate.get("origin", "")), -1),
                    1 if candidate.get("strength") == "hard" else 0,
                ),
            )
            expected_signals = (
                str(dominant.get("strength", "")),
                str(dominant.get("origin", "")),
                str(dominant.get("recall_mode", "")),
            )
            if (
                operation.strength,
                operation.origin,
                operation.recall_mode,
            ) != expected_signals:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}]",
                    "preserve_candidate_signals",
                    operation.operation,
                )
            if not operation.subject or not operation.statement or not operation.content:
                MemoryPipeline._raise_semantic_output_error(
                    f"operation[{index}]",
                    "non_empty_item_content",
                    operation.operation,
                )

            if operation.target_id is not None:
                target = active_items[operation.target_id]
                target_paths = tuple(
                    sorted(set(target.get("applies_to_paths", ())))
                )
                target_is_discussion = (
                    target.get("kind") == "discussion_context"
                    and target.get("thread_id") == selected[0].get("thread_id")
                )
                target_is_project = (
                    target.get("kind") in {"user_preference", "project_decision"}
                    and target.get("thread_id") is None
                )
                if target_paths != expected_paths or (
                    target_is_discussion if discussion_scope else target_is_project
                ) is False:
                    MemoryPipeline._raise_semantic_output_error(
                        f"operation[{index}].target_id",
                        "matching_target_scope",
                        operation.target_id,
                    )
                if (
                    target.get("origin") in {"explicit", "adopted_proposal"}
                    and operation.origin == "inferred_repetition"
                ):
                    MemoryPipeline._raise_semantic_output_error(
                        f"operation[{index}].target_id",
                        "inferred_cannot_replace_explicit",
                        operation.target_id,
                    )
        if handled != expected_ids:
            MemoryPipeline._raise_semantic_output_error(
                "operations",
                "process_every_candidate_once",
                result.operations,
            )

    @staticmethod
    def _raise_semantic_output_error(
        path: str,
        rule: str,
        value: Any,
    ) -> None:
        observed_length = (
            len(value)
            if isinstance(value, (str, list, tuple, dict))
            else None
        )
        raise MemoryOutputContractError(
            MemoryContractViolation(
                path=path,
                rule=rule,
                observed_type=MemoryPipeline._observed_type(value),
                observed_length=observed_length,
            )
        )

    @staticmethod
    def _violation_payload(
        violation: MemoryContractViolation,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "path": violation.path,
            "rule": violation.rule,
        }
        optional: dict[str, Any] = {
            "observed_type": violation.observed_type,
            "observed_length": violation.observed_length,
            "recognized_keys": list(violation.recognized_keys),
            "missing_keys": list(violation.missing_keys),
            "unknown_key_count": violation.unknown_key_count,
        }
        payload.update(
            {
                key: value
                for key, value in optional.items()
                if value not in ("", None, [], 0)
            }
        )
        return payload

    @staticmethod
    def _failure_diagnostic(
        batch: ClaimedJobBatch,
        phase: str,
        stage: str,
        exc: Exception,
        *,
        response: Any = None,
    ) -> MemoryFailureDiagnostic:
        attempt_count = max(job.attempt_count for job in batch.jobs)
        llm_call_count = max(job.llm_call_count for job in batch.jobs)
        response_id = MemoryPipeline._bounded_identifier(
            getattr(response, "response_id", None)
            or getattr(exc, "response_id", None)
            or getattr(exc, "request_id", None)
        )
        finish_reason = MemoryPipeline._normalized_finish_reason(
            getattr(response, "finish_reason", None)
            or getattr(exc, "finish_reason", None)
        )
        if isinstance(exc, MemoryOutputContractError):
            violation = exc.violation
            return MemoryFailureDiagnostic(
                category="output_contract",
                phase=phase,
                stage=stage,
                attempt_count=attempt_count,
                llm_call_count=llm_call_count,
                path=violation.path,
                rule=violation.rule,
                observed_type=violation.observed_type,
                observed_length=violation.observed_length,
                recognized_keys=violation.recognized_keys,
                missing_keys=violation.missing_keys,
                unknown_key_count=violation.unknown_key_count,
                response_id=response_id,
                finish_reason=finish_reason,
            )
        status_code = MemoryPipeline._numeric_status_code(exc)
        return MemoryFailureDiagnostic(
            category=MemoryPipeline._failure_category(exc, status_code),
            phase=phase,
            stage=stage,
            attempt_count=attempt_count,
            llm_call_count=llm_call_count,
            response_id=response_id,
            finish_reason=finish_reason,
            exception_type=type(exc).__name__[:128],
            status_code=status_code,
        )

    @staticmethod
    def _is_transient_primary_error(exc: Exception) -> bool:
        if isinstance(exc, LLMTransientError):
            return True
        if isinstance(exc, sqlite3.OperationalError):
            message = str(exc).lower()
            return "locked" in message or "busy" in message
        status_code = MemoryPipeline._numeric_status_code(exc)
        if status_code in {408, 409, 429} or (
            status_code is not None and 500 <= status_code <= 599
        ):
            return True
        exception_name = type(exc).__name__.lower()
        return "timeout" in exception_name or "connection" in exception_name

    @staticmethod
    def _failure_category(exc: Exception, status_code: int | None) -> str:
        if isinstance(exc, ContextCapacityError):
            return "capacity"
        if isinstance(exc, MemoryLeaseError):
            return "lease"
        if isinstance(exc, sqlite3.OperationalError):
            return "sqlite"
        if isinstance(exc, (LLMProtocolError, LLMResponseError)):
            return "provider"
        exception_name = type(exc).__name__.lower()
        if (
            status_code is not None
            or "timeout" in exception_name
            or "connection" in exception_name
            or type(exc).__module__.startswith("openai")
        ):
            return "provider"
        return "internal"

    @staticmethod
    def _numeric_status_code(exc: Exception) -> int | None:
        value = getattr(exc, "status_code", None)
        if isinstance(value, int):
            return value
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
        return value if isinstance(value, int) else None

    @staticmethod
    def _bounded_identifier(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized[:128] or None

    @staticmethod
    def _normalized_finish_reason(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = "".join(
            character
            for character in value.strip().lower()
            if character.isalnum() or character in {"_", "-"}
        )
        return normalized[:64] or None

    @staticmethod
    def _observed_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, (int, float)):
            return "number"
        if isinstance(value, (list, tuple)):
            return "array"
        if isinstance(value, dict):
            return "object"
        return "unknown"

    @staticmethod
    def _serialize_diagnostic(diagnostic: MemoryFailureDiagnostic) -> str:
        return json.dumps(
            diagnostic.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
