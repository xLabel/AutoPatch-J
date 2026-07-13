from __future__ import annotations

import json
from typing import Any

from .constants import MAX_COMPACTION_CHARS, MEMORY_KINDS
from .errors import MemoryContractError
from .models import (
    CandidateSource,
    ConsolidationOperation,
    ConsolidationResult,
    ExtractionCandidateInput,
    ExtractionResult,
)
from .text_utils import compact_text


_EXTRACTION_KEYS = {"thread_compaction", "candidates"}
_CANDIDATE_KEYS = {"kind", "title", "content", "aliases", "sources"}
_SOURCE_KEYS = {"turn_id", "role", "quote"}
_CONSOLIDATION_KEYS = {"operations"}
_OPERATION_KEYS = {
    "operation",
    "candidate_ids",
    "target_id",
    "title",
    "content",
    "synopsis",
    "aliases",
    "keywords",
}


def parse_extraction_response(content: str) -> ExtractionResult:
    raw = _parse_object(content, "extraction")
    _require_exact_keys(raw, _EXTRACTION_KEYS, "extraction")
    compaction = _required_string(raw["thread_compaction"], "thread_compaction")
    if len(compaction) > MAX_COMPACTION_CHARS:
        raise MemoryContractError("thread_compaction 超过 4000 字符")
    candidates_raw = raw["candidates"]
    if not isinstance(candidates_raw, list) or len(candidates_raw) > 32:
        raise MemoryContractError("candidates 必须是最多 32 项的数组")
    candidates: list[ExtractionCandidateInput] = []
    for index, item in enumerate(candidates_raw):
        if not isinstance(item, dict):
            raise MemoryContractError(f"candidate[{index}] 必须是对象")
        _require_exact_keys(item, _CANDIDATE_KEYS, f"candidate[{index}]")
        kind = _required_string(item["kind"], f"candidate[{index}].kind")
        if kind not in MEMORY_KINDS:
            raise MemoryContractError(f"不允许的 candidate kind: {kind}")
        title = _bounded_string(item["title"], 160, f"candidate[{index}].title")
        body = _bounded_string(item["content"], 4_000, f"candidate[{index}].content")
        aliases = _string_array(item["aliases"], 12, 160, f"candidate[{index}].aliases")
        sources_raw = item["sources"]
        if not isinstance(sources_raw, list) or not sources_raw or len(sources_raw) > 8:
            raise MemoryContractError("candidate sources 必须是 1..8 项数组")
        sources: list[CandidateSource] = []
        for source_index, source in enumerate(sources_raw):
            if not isinstance(source, dict):
                raise MemoryContractError("candidate source 必须是对象")
            _require_exact_keys(source, _SOURCE_KEYS, "candidate source")
            role = _required_string(source["role"], "source.role")
            if role not in {"user", "assistant"}:
                raise MemoryContractError("source.role 只能是 user 或 assistant")
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
                title=title,
                content=body,
                aliases=aliases,
                sources=tuple(sources),
            )
        )
    return ExtractionResult(compaction, tuple(candidates))


def parse_consolidation_response(content: str) -> ConsolidationResult:
    raw = _parse_object(content, "consolidation")
    _require_exact_keys(raw, _CONSOLIDATION_KEYS, "consolidation")
    operations_raw = raw["operations"]
    if not isinstance(operations_raw, list) or len(operations_raw) > 32:
        raise MemoryContractError("operations 必须是最多 32 项的数组")
    operations: list[ConsolidationOperation] = []
    for index, item in enumerate(operations_raw):
        if not isinstance(item, dict):
            raise MemoryContractError(f"operation[{index}] 必须是对象")
        _require_exact_keys(item, _OPERATION_KEYS, f"operation[{index}]")
        operation = _required_string(item["operation"], "operation")
        if operation not in {"create", "revise", "supersede", "reject"}:
            raise MemoryContractError(f"不允许的 operation: {operation}")
        candidate_ids = _string_array(item["candidate_ids"], 32, 160, "candidate_ids")
        if not candidate_ids:
            raise MemoryContractError("candidate_ids 不得为空")
        target_raw = item["target_id"]
        if target_raw is not None and not isinstance(target_raw, str):
            raise MemoryContractError("target_id 必须是 string 或 null")
        target_id = target_raw.strip() if isinstance(target_raw, str) else None
        if target_id == "":
            target_id = None
        title = _optional_bounded_string(item["title"], 160, "title")
        body = _optional_bounded_string(item["content"], 4_000, "content")
        synopsis = _optional_bounded_string(item["synopsis"], 400, "synopsis")
        aliases = _string_array(item["aliases"], 16, 160, "aliases")
        keywords = _string_array(item["keywords"], 24, 160, "keywords")
        operations.append(
            ConsolidationOperation(
                operation=operation,
                candidate_ids=candidate_ids,
                target_id=target_id,
                title=title,
                content=body,
                synopsis=synopsis,
                aliases=aliases,
                keywords=keywords,
            )
        )
    return ConsolidationResult(tuple(operations))


def _parse_object(content: str, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise MemoryContractError(f"{label} 输出不是合法 JSON 对象") from exc
    if not isinstance(raw, dict):
        raise MemoryContractError(f"{label} 输出必须是 JSON 对象")
    return raw


def _require_exact_keys(raw: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    if actual != expected:
        raise MemoryContractError(
            f"{label} 字段不匹配，missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryContractError(f"{label} 必须是非空 string")
    return value.strip()


def _bounded_string(value: Any, limit: int, label: str) -> str:
    text = _required_string(value, label)
    if len(text) > limit:
        raise MemoryContractError(f"{label} 超过 {limit} 字符")
    return text


def _optional_bounded_string(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str):
        raise MemoryContractError(f"{label} 必须是 string")
    if len(value) > limit:
        raise MemoryContractError(f"{label} 超过 {limit} 字符")
    return value.strip()


def _string_array(value: Any, limit: int, item_limit: int, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise MemoryContractError(f"{label} 必须是最多 {limit} 项数组")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > item_limit:
            raise MemoryContractError(f"{label} 包含非法 string")
        text = compact_text(item, item_limit)
        if text not in result:
            result.append(text)
    return tuple(result)
