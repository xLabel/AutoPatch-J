from __future__ import annotations

from typing import Any

from .schema import MAX_LABEL, MAX_SUMMARY, clip_text, generate_id, normalize_string_list, now_iso


class MemoryDeltaApplier:
    """对短 LLM 生成的 memory delta 做程序侧硬校验并写入内存对象。"""

    def apply(
        self,
        memory: dict[str, Any],
        delta: dict[str, Any],
        allowed_project_evidence_ids: set[str] | None = None,
    ) -> bool:
        if not isinstance(delta, dict):
            return False

        changed = False
        changed = self._apply_turn_summaries(memory, delta.get("turn_summaries")) or changed
        changed = self._apply_topic_operations(memory, delta.get("topic_operations")) or changed
        changed = (
            self._apply_long_term_operations(
                memory,
                delta.get("long_term_operations"),
                allowed_project_evidence_ids=allowed_project_evidence_ids,
            )
            or changed
        )
        return changed

    def _apply_turn_summaries(self, memory: dict[str, Any], operations: Any) -> bool:
        if not isinstance(operations, list):
            return False
        turns = {turn["id"]: turn for turn in memory["working_memory"]["recent_turns"]}
        changed = False
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            turn = turns.get(str(operation.get("turn_id", "")))
            summary = str(operation.get("summary", "")).strip()
            if not turn or not summary:
                continue
            turn["summary"] = clip_text(summary, MAX_SUMMARY)
            turn["summary_status"] = "ready"
            changed = True
        return changed

    def _apply_topic_operations(self, memory: dict[str, Any], operations: Any) -> bool:
        if not isinstance(operations, list):
            return False
        topics = memory["working_memory"]["active_topics"]
        by_id = {topic["id"]: topic for topic in topics}
        changed = False
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            op = operation.get("operation")
            if op == "create_new":
                label = str(operation.get("label", "")).strip()
                summary = str(operation.get("summary", "")).strip()
                if not label or not summary:
                    continue
                topics.append(
                    {
                        "id": generate_id("topic"),
                        "label": clip_text(label, MAX_LABEL),
                        "summary": clip_text(summary, MAX_SUMMARY),
                        "related_turn_ids": self._valid_related_turn_ids(memory, operation.get("related_turn_ids")),
                        "last_touched_at": now_iso(),
                    }
                )
                changed = True
            elif op == "update_existing":
                topic = by_id.get(str(operation.get("target_id", "")))
                summary = str(operation.get("summary", "")).strip()
                if not topic or not summary:
                    continue
                topic["summary"] = clip_text(summary, MAX_SUMMARY)
                topic["last_touched_at"] = now_iso()
                changed = True
        return changed

    def _apply_long_term_operations(
        self,
        memory: dict[str, Any],
        operations: Any,
        allowed_project_evidence_ids: set[str] | None,
    ) -> bool:
        if not isinstance(operations, list):
            return False
        changed = False
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            op = operation.get("operation")
            if op == "create_new":
                changed = self._create_long_term_item(memory, operation, allowed_project_evidence_ids) or changed
            elif op == "update_existing":
                changed = self._update_long_term_item(memory, operation, allowed_project_evidence_ids) or changed
        return changed

    def _create_long_term_item(
        self,
        memory: dict[str, Any],
        operation: dict[str, Any],
        allowed_project_evidence_ids: set[str] | None,
    ) -> bool:
        target_list = self._long_term_target(memory, operation.get("type"))
        if target_list is None:
            return False
        label = str(operation.get("label", "")).strip()
        summary = str(operation.get("summary", "")).strip()
        source = str(operation.get("source", "")).strip()
        item_type = str(operation.get("type"))
        if not self._is_allowed_new_long_term_item(
            item_type=item_type,
            label=label,
            summary=summary,
            source=source,
            evidence_id=operation.get("evidence_id"),
            allowed_project_evidence_ids=allowed_project_evidence_ids,
        ):
            return False

        now = now_iso()
        target_list.append(
            {
                "id": generate_id("mem"),
                "type": item_type,
                "label": clip_text(label, MAX_LABEL),
                "summary": clip_text(summary, MAX_SUMMARY),
                "status": "active",
                "source": source,
                "created_at": now,
                "updated_at": now,
            }
        )
        return True

    def _update_long_term_item(
        self,
        memory: dict[str, Any],
        operation: dict[str, Any],
        allowed_project_evidence_ids: set[str] | None,
    ) -> bool:
        item = self._find_long_term_item(memory, operation.get("target_id"))
        summary = str(operation.get("summary", "")).strip()
        if not item or not summary:
            return False
        if not self._is_allowed_long_term_update(
            item=item,
            operation=operation,
            allowed_project_evidence_ids=allowed_project_evidence_ids,
        ):
            return False
        item["summary"] = clip_text(summary, MAX_SUMMARY)
        item["updated_at"] = now_iso()
        return True

    def _is_allowed_new_long_term_item(
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

    def _is_allowed_long_term_update(
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

    def _long_term_target(self, memory: dict[str, Any], item_type: Any) -> list[dict[str, Any]] | None:
        if item_type == "durable_preference":
            return memory["long_term_memory"]["durable_preferences"]
        if item_type == "project_fact":
            return memory["long_term_memory"]["project_facts"]
        return None

    def _find_long_term_item(self, memory: dict[str, Any], target_id: Any) -> dict[str, Any] | None:
        target = str(target_id or "")
        for collection in (
            memory["long_term_memory"]["durable_preferences"],
            memory["long_term_memory"]["project_facts"],
        ):
            for item in collection:
                if item["id"] == target and item["status"] == "active":
                    return item
        return None

    def _valid_related_turn_ids(self, memory: dict[str, Any], raw_ids: Any) -> list[str]:
        valid_ids = {turn["id"] for turn in memory["working_memory"]["recent_turns"]}
        return [turn_id for turn_id in normalize_string_list(raw_ids, 20, 120) if turn_id in valid_ids]
