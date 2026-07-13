from __future__ import annotations

import json

import pytest

from autopatch_j.core.memory.contracts import (
    parse_consolidation_response,
    parse_extraction_response,
)
from autopatch_j.core.memory.errors import MemoryContractError


@pytest.mark.parametrize(
    "content",
    (
        '```json\n{"thread_compaction": "讨论", "candidates": []}\n```',
        json.dumps([]),
        json.dumps({"candidates": []}),
        json.dumps(
            {"thread_compaction": "讨论", "candidates": [], "unexpected": True}
        ),
    ),
)
def test_extraction_contract_rejects_non_object_or_non_exact_envelope(
    content: str,
) -> None:
    with pytest.raises(MemoryContractError):
        parse_extraction_response(content)


def _consolidation_operation(operation: str = "create") -> dict[str, object]:
    return {
        "operation": operation,
        "candidate_ids": ["candidate_1"],
        "target_id": None,
        "title": "简洁回答",
        "content": "用户偏好简洁回答",
        "synopsis": "保持简洁",
        "aliases": [],
        "keywords": [],
    }


@pytest.mark.parametrize(
    "content",
    (
        json.dumps([{"operations": []}]),
        json.dumps({"operations": [], "unexpected": True}),
        json.dumps({"operations": [_consolidation_operation("merge")]}),
    ),
)
def test_consolidation_contract_rejects_non_object_extra_keys_or_illegal_operation(
    content: str,
) -> None:
    with pytest.raises(MemoryContractError):
        parse_consolidation_response(content)
