from __future__ import annotations

from typing import Any


class LongTermMemoryPolicy:
    """Program-side guardrails for durable preferences and repo-verified project facts."""

    def allows_new_item(
        self,
        item_type: str,
        label: str,
        summary: str,
        source: str,
        evidence_id: Any,
        allowed_project_evidence_ids: set[str] | None,
    ) -> bool:
        if not label or not summary:
            return False
        if item_type == "durable_preference":
            return source == "user_explicit"
        if item_type == "project_fact":
            return (
                source == "repo_verified"
                and allowed_project_evidence_ids is not None
                and str(evidence_id or "") in allowed_project_evidence_ids
            )
        return False

    def allows_update(
        self,
        item: dict[str, Any],
        operation: dict[str, Any],
        allowed_project_evidence_ids: set[str] | None,
    ) -> bool:
        if item.get("type") != "project_fact":
            return True
        return (
            operation.get("source") == "repo_verified"
            and allowed_project_evidence_ids is not None
            and str(operation.get("evidence_id") or "") in allowed_project_evidence_ids
        )
