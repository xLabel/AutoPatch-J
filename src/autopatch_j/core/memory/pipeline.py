from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from autopatch_j.llm.diagnostics import format_raw_llm_exception
from autopatch_j.llm.context_window import (
    ContextCapacityError,
    ModelContextProfile,
    clip_text_head_tail,
    estimate_request_tokens,
    estimate_text_tokens,
)
from autopatch_j.llm.options import LLMCallPurpose

from .contracts import parse_consolidation_response, parse_extraction_response
from .models import ClaimedJobBatch, ExtractionSourceInput, JobKind
from .prompts import MEMORY_CONSOLIDATION_SYSTEM_PROMPT, MEMORY_EXTRACTION_SYSTEM_PROMPT
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


class MemoryPipeline:
    def __init__(self, store: MemoryStore, llm: MemoryLLM, owner: str) -> None:
        self.store = store
        self.llm = llm
        self.owner = owner

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
            result = parse_extraction_response(response.content)
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
        except ContextCapacityError as exc:
            return self._complete_capacity_skip(batch, "extraction", exc)
        except Exception as exc:
            summary = self._failure_summary("extraction", exc)
            self._record_failure(batch, summary)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=0,
                failed=len(batch.jobs),
                error=summary,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.EXTRACTION.value,
            )

    def _process_consolidation(self, batch: ClaimedJobBatch) -> PipelineStepResult:
        try:
            payload = self._fit_consolidation_payload(
                self.store.consolidation_payload(batch)
            )
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
            result = parse_consolidation_response(response.content)
            self.store.apply_consolidation(batch, result)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=len(batch.jobs),
                failed=0,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.CONSOLIDATION.value,
            )
        except ContextCapacityError as exc:
            return self._complete_capacity_skip(batch, "consolidation", exc)
        except Exception as exc:
            summary = self._failure_summary("consolidation", exc)
            self._record_failure(batch, summary)
            return PipelineStepResult(
                processed=len(batch.jobs),
                succeeded=0,
                failed=len(batch.jobs),
                error=summary,
                processed_job_ids=tuple(job.id for job in batch.jobs),
                job_kind=JobKind.CONSOLIDATION.value,
            )

    def _record_failure(self, batch: ClaimedJobBatch, summary: str) -> None:
        try:
            self.store.record_job_failure(batch, summary)
        except Exception:
            # clear/lease fencing errors are already the authoritative result; a stale
            # worker must never turn them into a second write.
            return

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

    def _fit_extraction_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = getattr(self.llm, "context_profile", None)
        if not isinstance(profile, ModelContextProfile):
            return payload
        projected = deepcopy(payload)
        if self._payload_fits(
            MEMORY_EXTRACTION_SYSTEM_PROMPT,
            projected,
            profile.input_capacity,
        ):
            return projected
        evidence = projected.get("recent_repair_evidence")
        while isinstance(evidence, list) and evidence:
            evidence.pop(0)
            if self._payload_fits(
                MEMORY_EXTRACTION_SYSTEM_PROMPT,
                projected,
                profile.input_capacity,
            ):
                return projected
        if projected.get("adjacent_previous_turn") is not None:
            projected["adjacent_previous_turn"] = None
            if self._payload_fits(
                MEMORY_EXTRACTION_SYSTEM_PROMPT,
                projected,
                profile.input_capacity,
            ):
                return projected
        if projected.get("previous_compaction"):
            projected["previous_compaction"] = ""
            if self._payload_fits(
                MEMORY_EXTRACTION_SYSTEM_PROMPT,
                projected,
                profile.input_capacity,
            ):
                return projected
        for field in ("assistant", "user"):
            if self._clip_largest_turn_fields(
                projected,
                field=field,
                system_prompt=MEMORY_EXTRACTION_SYSTEM_PROMPT,
                input_capacity=profile.input_capacity,
            ):
                return projected
        raise ContextCapacityError(
            "Memory extraction 固定 contract 与 primary turn 投影仍超过 input capacity。"
        )

    def _fit_consolidation_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = getattr(self.llm, "context_profile", None)
        if not isinstance(profile, ModelContextProfile):
            return payload
        projected = deepcopy(payload)
        if not self._payload_fits(
            MEMORY_CONSOLIDATION_SYSTEM_PROMPT,
            projected,
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
    ) -> bool:
        turns = payload.get("turns")
        if not isinstance(turns, list):
            return False
        while not self._payload_fits(system_prompt, payload, input_capacity):
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
                self._payload_tokens(system_prompt, payload) - input_capacity,
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
    def _failure_summary(phase: str, exc: Exception) -> str:
        return f"Memory {phase} failed: {format_raw_llm_exception(exc)}"
