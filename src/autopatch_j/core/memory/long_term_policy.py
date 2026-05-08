from __future__ import annotations

from typing import Any


class LongTermMemoryPolicy:
    """Program-side guardrails for durable preferences and project discussion notes."""

    def allows_new_item(
        self,
        item_type: str,
        label: str,
        summary: str,
        source: str,
    ) -> bool:
        if not label or not summary:
            return False
        if item_type == "durable_preference":
            return source == "user_explicit"
        if item_type == "project_note":
            return source == "conversation_summary"
        return False

    def allows_update(self, item: dict[str, Any], operation: dict[str, Any]) -> bool:
        if item.get("type") == "durable_preference":
            return operation.get("source") in {None, "", "user_explicit"}
        if item.get("type") == "project_note":
            return operation.get("source") == "conversation_summary"
        return False
