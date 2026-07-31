from __future__ import annotations

import json
from typing import Any

from autopatch_j.llm.context_window import estimate_text_tokens

from .constants import (
    MAX_COMPACTION_CHARS,
    MAX_MEMORY_STATEMENT_TOKENS,
    MEMORY_KINDS,
    MEMORY_ORIGINS,
    MEMORY_RECALL_MODES,
    MEMORY_STRENGTHS,
)
from .errors import (
    MemoryContractViolation,
    MemoryOutputContractError,
)
from .models import (
    CandidateSource,
    ConsolidationOperation,
    ConsolidationResult,
    ExtractionCandidateInput,
    ExtractionResult,
)
from .text_utils import compact_text


_EXTRACTION_KEYS = {"thread_compaction", "candidates"}
_CANDIDATE_KEYS = {
    "kind",
    "subject",
    "statement",
    "content",
    "strength",
    "origin",
    "recall_mode",
    "applies_to_paths",
    "aliases",
    "keywords",
    "sources",
}
_SOURCE_KEYS = {"turn_id", "role", "quote"}
_CONSOLIDATION_KEYS = {"operations"}
_OPERATION_KEYS = {
    "operation",
    "candidate_ids",
    "target_id",
    "kind",
    "subject",
    "statement",
    "content",
    "strength",
    "origin",
    "recall_mode",
    "applies_to_paths",
    "aliases",
    "keywords",
}


def parse_extraction_response(content: str) -> ExtractionResult:
    raw = _parse_object(content, "extraction")
    _require_exact_keys(raw, _EXTRACTION_KEYS, "extraction")
    compaction = _required_string(raw["thread_compaction"], "thread_compaction")
    if len(compaction) > MAX_COMPACTION_CHARS:
        _raise_output_error(
            "thread_compaction",
            "max_length",
            compaction,
        )
    candidates_raw = raw["candidates"]
    if not isinstance(candidates_raw, list) or len(candidates_raw) > 32:
        _raise_output_error("candidates", "array_max_32", candidates_raw)
    candidates: list[ExtractionCandidateInput] = []
    for index, item in enumerate(candidates_raw):
        if not isinstance(item, dict):
            _raise_output_error(f"candidate[{index}]", "object_required", item)
        _require_exact_keys(item, _CANDIDATE_KEYS, f"candidate[{index}]")
        kind = _required_string(item["kind"], f"candidate[{index}].kind")
        if kind not in MEMORY_KINDS:
            _raise_output_error(f"candidate[{index}].kind", "allowed_enum", kind)
        subject = _bounded_string(item["subject"], 160, f"candidate[{index}].subject")
        statement = _memory_statement(
            item["statement"], f"candidate[{index}].statement"
        )
        body = _bounded_string(item["content"], 4_000, f"candidate[{index}].content")
        strength = _enum_string(
            item["strength"], MEMORY_STRENGTHS, f"candidate[{index}].strength"
        )
        origin = _enum_string(
            item["origin"], MEMORY_ORIGINS, f"candidate[{index}].origin"
        )
        recall_mode = _enum_string(
            item["recall_mode"],
            MEMORY_RECALL_MODES,
            f"candidate[{index}].recall_mode",
        )
        applies_to_paths = _string_array(
            item["applies_to_paths"],
            10,
            400,
            f"candidate[{index}].applies_to_paths",
        )
        aliases = _string_array(item["aliases"], 12, 160, f"candidate[{index}].aliases")
        keywords = _string_array(
            item["keywords"], 24, 160, f"candidate[{index}].keywords"
        )
        sources_raw = item["sources"]
        if not isinstance(sources_raw, list) or not sources_raw or len(sources_raw) > 8:
            _raise_output_error(
                f"candidate[{index}].sources",
                "array_length_1_8",
                sources_raw,
            )
        sources: list[CandidateSource] = []
        for source_index, source in enumerate(sources_raw):
            if not isinstance(source, dict):
                _raise_output_error(
                    f"candidate[{index}].sources[{source_index}]",
                    "object_required",
                    source,
                )
            _require_exact_keys(source, _SOURCE_KEYS, "candidate source")
            role = _required_string(source["role"], "source.role")
            if role not in {"user", "assistant"}:
                _raise_output_error("source.role", "allowed_enum", role)
            sources.append(
                CandidateSource(
                    turn_id=_required_string(source["turn_id"], "source.turn_id"),
                    role=role,
                    quote=_bounded_string(source["quote"], 2_000, "source.quote"),
                )
            )
        candidates.append(
            ExtractionCandidateInput(
                kind=kind,
                subject=subject,
                statement=statement,
                content=body,
                strength=strength,
                origin=origin,
                recall_mode=recall_mode,
                applies_to_paths=applies_to_paths,
                aliases=aliases,
                keywords=keywords,
                sources=tuple(sources),
            )
        )
    return ExtractionResult(compaction, tuple(candidates))


def parse_consolidation_response(content: str) -> ConsolidationResult:
    raw = _parse_object(content, "consolidation")
    _require_exact_keys(raw, _CONSOLIDATION_KEYS, "consolidation")
    operations_raw = raw["operations"]
    if not isinstance(operations_raw, list) or len(operations_raw) > 32:
        _raise_output_error("operations", "array_max_32", operations_raw)
    operations: list[ConsolidationOperation] = []
    for index, item in enumerate(operations_raw):
        if not isinstance(item, dict):
            _raise_output_error(f"operation[{index}]", "object_required", item)
        _require_exact_keys(item, _OPERATION_KEYS, f"operation[{index}]")
        operation = _required_string(item["operation"], "operation")
        if operation not in {"create", "revise", "supersede", "reject"}:
            _raise_output_error(f"operation[{index}].operation", "allowed_enum", operation)
        candidate_ids = _string_array(item["candidate_ids"], 32, 160, "candidate_ids")
        if not candidate_ids:
            _raise_output_error(
                f"operation[{index}].candidate_ids",
                "non_empty_array",
                item["candidate_ids"],
            )
        target_raw = item["target_id"]
        if target_raw is not None and not isinstance(target_raw, str):
            _raise_output_error(
                f"operation[{index}].target_id",
                "string_or_null",
                target_raw,
            )
        target_id = target_raw.strip() if isinstance(target_raw, str) else None
        if target_id == "":
            target_id = None
        kind = _optional_enum_string(item["kind"], MEMORY_KINDS, "kind")
        subject = _optional_bounded_string(item["subject"], 160, "subject")
        statement = _optional_memory_statement(item["statement"], "statement")
        body = _optional_bounded_string(item["content"], 4_000, "content")
        strength = _optional_enum_string(item["strength"], MEMORY_STRENGTHS, "strength")
        origin = _optional_enum_string(item["origin"], MEMORY_ORIGINS, "origin")
        recall_mode = _optional_enum_string(
            item["recall_mode"], MEMORY_RECALL_MODES, "recall_mode"
        )
        applies_to_paths = _string_array(item["applies_to_paths"], 10, 400, "applies_to_paths")
        aliases = _string_array(item["aliases"], 16, 160, "aliases")
        keywords = _string_array(item["keywords"], 24, 160, "keywords")
        operations.append(
            ConsolidationOperation(
                operation=operation,
                candidate_ids=candidate_ids,
                target_id=target_id,
                kind=kind,
                subject=subject,
                statement=statement,
                content=body,
                strength=strength,
                origin=origin,
                recall_mode=recall_mode,
                applies_to_paths=applies_to_paths,
                aliases=aliases,
                keywords=keywords,
            )
        )
    return ConsolidationResult(tuple(operations))


def _parse_object(content: str, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        _raise_output_error(label, "valid_json_object", content)
    if not isinstance(raw, dict):
        _raise_output_error(label, "object_required", raw)
    return raw


def _require_exact_keys(raw: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    if actual != expected:
        raise MemoryOutputContractError(
            MemoryContractViolation(
                path=label,
                rule="exact_keys",
                observed_type="object",
                observed_length=len(raw),
                recognized_keys=tuple(sorted(actual & expected)),
                missing_keys=tuple(sorted(expected - actual)),
                unknown_key_count=len(actual - expected),
            )
        )


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _raise_output_error(label, "non_empty_string", value)
    return value.strip()


def _bounded_string(value: Any, limit: int, label: str) -> str:
    text = _required_string(value, label)
    if len(text) > limit:
        _raise_output_error(label, f"max_length_{limit}", text)
    return text


def _optional_bounded_string(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str):
        _raise_output_error(label, "string_required", value)
    if len(value) > limit:
        _raise_output_error(label, f"max_length_{limit}", value)
    return value.strip()


def _memory_statement(value: Any, label: str) -> str:
    text = _required_string(value, label)
    if estimate_text_tokens(text) > MAX_MEMORY_STATEMENT_TOKENS:
        _raise_output_error(
            label,
            f"max_tokens_{MAX_MEMORY_STATEMENT_TOKENS}",
            text,
        )
    return text


def _optional_memory_statement(value: Any, label: str) -> str:
    if not isinstance(value, str):
        _raise_output_error(label, "string_required", value)
    text = value.strip()
    if text and estimate_text_tokens(text) > MAX_MEMORY_STATEMENT_TOKENS:
        _raise_output_error(
            label,
            f"max_tokens_{MAX_MEMORY_STATEMENT_TOKENS}",
            text,
        )
    return text


def _enum_string(value: Any, allowed: set[str], label: str) -> str:
    text = _required_string(value, label)
    if text not in allowed:
        _raise_output_error(label, "allowed_enum", text)
    return text


def _optional_enum_string(value: Any, allowed: set[str], label: str) -> str:
    if not isinstance(value, str):
        _raise_output_error(label, "string_required", value)
    text = value.strip()
    if text and text not in allowed:
        _raise_output_error(label, "allowed_enum_or_empty", text)
    return text


def _string_array(value: Any, limit: int, item_limit: int, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit:
        _raise_output_error(label, f"array_max_{limit}", value)
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > item_limit:
            _raise_output_error(label, f"non_empty_string_max_{item_limit}", item)
        text = compact_text(item, item_limit)
        if text not in result:
            result.append(text)
    return tuple(result)


def _raise_output_error(path: str, rule: str, value: Any) -> None:
    observed_length = (
        len(value)
        if isinstance(value, (str, list, tuple, dict))
        else None
    )
    raise MemoryOutputContractError(
        MemoryContractViolation(
            path=path,
            rule=rule,
            observed_type=_json_type(value),
            observed_length=observed_length,
        )
    )


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"
