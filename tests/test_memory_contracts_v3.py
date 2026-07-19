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
        "kind": "user_preference",
        "subject": "response style",
        "statement": "回答默认保持简洁",
        "content": "用户偏好简洁回答",
        "strength": "hard",
        "origin": "explicit",
        "recall_mode": "always",
        "applies_to_paths": [],
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


def test_v3_contract_parses_typed_candidate_and_operation() -> None:
    extraction = parse_extraction_response(
        json.dumps(
            {
                "thread_compaction": "用户确定回答风格",
                "candidates": [
                    {
                        "kind": "user_preference",
                        "subject": "response style",
                        "statement": "回答默认保持简洁",
                        "content": "用户希望项目讨论中的回答保持简洁。",
                        "strength": "hard",
                        "origin": "explicit",
                        "recall_mode": "always",
                        "applies_to_paths": [],
                        "aliases": ["回答风格"],
                        "keywords": ["简洁"],
                        "sources": [
                            {
                                "turn_id": "turn_1",
                                "role": "user",
                                "quote": "以后默认简洁回答",
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    consolidation = parse_consolidation_response(
        json.dumps({"operations": [_consolidation_operation()]}, ensure_ascii=False)
    )

    assert extraction.candidates[0].subject == "response style"
    assert extraction.candidates[0].origin == "explicit"
    assert consolidation.operations[0].statement == "回答默认保持简洁"
