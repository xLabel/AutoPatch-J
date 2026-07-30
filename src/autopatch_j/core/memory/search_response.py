from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from autopatch_j.llm.context_window import estimate_messages_tokens

from .models import MemorySearchHit


MEMORY_SEARCH_CALL_ID_PLACEHOLDER = "0" * 32


def build_memory_search_response(
    query: str,
    hits: Sequence[MemorySearchHit],
) -> tuple[dict[str, Any], str]:
    payload = {
        "query": query,
        "hits": [
            {
                "id": hit.id,
                "kind": hit.kind,
                "subject": hit.subject,
                "statement": hit.statement,
                "match_type": hit.match_type,
            }
            for hit in hits
        ],
    }
    if not payload["hits"]:
        return payload, "未找到与查询相关的可用 Memory。"
    return (
        payload,
        "Memory 搜索结果：\n"
        + json.dumps(payload, ensure_ascii=False, indent=2, default=str),
    )


def estimate_memory_search_response_tokens(
    query: str,
    hits: Sequence[MemorySearchHit],
) -> int:
    _, message = build_memory_search_response(query, hits)
    return estimate_messages_tokens(
        [
            {
                "role": "tool",
                "tool_call_id": MEMORY_SEARCH_CALL_ID_PLACEHOLDER,
                "content": message,
            }
        ]
    )
