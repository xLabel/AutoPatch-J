from __future__ import annotations

from typing import Any

from .constants import MAX_LABEL, MAX_SUMMARY
from .long_term_policy import LongTermMemoryPolicy
from .text_utils import clip_text, generate_id, normalize_string_list, now_iso


class MemoryDeltaApplier:
    """对短 LLM 生成的 memory delta 做程序侧硬校验并写入内存对象。"""

    def __init__(self, long_term_policy: LongTermMemoryPolicy | None = None) -> None:
        self.long_term_policy = long_term_policy or LongTermMemoryPolicy()

    def apply(self, memory: dict[str, Any], delta: dict[str, Any]) -> bool:
        if not isinstance(delta, dict):
            return False

        episode_ids = self._episode_ids(memory)
        changed = False
        changed = self._apply_episode_summaries(memory, delta.get("episode_summaries")) or changed
        changed = self._apply_topic_operations(memory, delta.get("topic_operations"), episode_ids) or changed
        changed = self._apply_semantic_operations(memory, delta.get("semantic_operations"), episode_ids) or changed
        changed = (
            self._apply_procedural_operations(memory, delta.get("procedural_operations"), episode_ids)
            or changed
        )
        if changed:
            self._refresh_pending_episode_ids(memory)
            memory["maintenance"]["last_consolidated_at"] = now_iso()
        return changed

    def _apply_episode_summaries(self, memory: dict[str, Any], operations: Any) -> bool:
        if not isinstance(operations, list):
            return False
        episodes = {
            episode["id"]: episode
            for episode in memory["episodic_memory"]["episodes"]
        }
        changed = False
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            episode = episodes.get(str(operation.get("episode_id", "")))
            summary = str(operation.get("summary", "")).strip()
            if not episode or not summary:
                continue
            episode["summary"] = clip_text(summary, MAX_SUMMARY)
            episode["summary_status"] = "ready"
            changed = True
        return changed

    def _apply_topic_operations(
        self,
        memory: dict[str, Any],
        operations: Any,
        episode_ids: set[str],
    ) -> bool:
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
                        "related_episode_ids": self._valid_source_episode_ids(
                            operation.get("related_episode_ids"),
                            episode_ids,
                        ),
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
                related_ids = self._valid_source_episode_ids(
                    operation.get("related_episode_ids"),
                    episode_ids,
                )
                if related_ids:
                    topic["related_episode_ids"] = related_ids
                topic["last_touched_at"] = now_iso()
                changed = True
        return changed

    def _apply_semantic_operations(
        self,
        memory: dict[str, Any],
        operations: Any,
        episode_ids: set[str],
    ) -> bool:
        if not isinstance(operations, list):
            return False
        changed = False
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            target_list = self._semantic_target(memory, operation.get("type"))
            if target_list is None:
                continue
            changed = self._apply_long_term_operation(target_list, operation, episode_ids, semantic=True) or changed
        return changed

    def _apply_procedural_operations(
        self,
        memory: dict[str, Any],
        operations: Any,
        episode_ids: set[str],
    ) -> bool:
        if not isinstance(operations, list):
            return False
        changed = False
        for operation in operations:
            if not isinstance(operation, dict):
                continue
            if operation.get("type") != "collaboration_preference":
                continue
            target_list = memory["procedural_memory"]["collaboration_preferences"]
            changed = self._apply_long_term_operation(
                target_list,
                operation,
                episode_ids,
                semantic=False,
            ) or changed
        return changed

    def _apply_long_term_operation(
        self,
        target_list: list[dict[str, Any]],
        operation: dict[str, Any],
        episode_ids: set[str],
        semantic: bool,
    ) -> bool:
        op = operation.get("operation")
        if op == "create_new":
            return self._create_long_term_item(target_list, operation, episode_ids, semantic)
        if op == "update_existing":
            return self._update_long_term_item(target_list, operation, episode_ids, semantic)
        if op == "deactivate":
            return self._deactivate_long_term_item(target_list, operation)
        return False

    def _create_long_term_item(
        self,
        target_list: list[dict[str, Any]],
        operation: dict[str, Any],
        episode_ids: set[str],
        semantic: bool,
    ) -> bool:
        item_type = str(operation.get("type", "")).strip()
        label = str(operation.get("label", "")).strip()
        summary = str(operation.get("summary", "")).strip()
        source_episode_ids = self._valid_source_episode_ids(operation.get("source_episode_ids"), episode_ids)
        allowed = (
            self.long_term_policy.allows_semantic_item(item_type, label, summary, source_episode_ids)
            if semantic
            else self.long_term_policy.allows_procedural_item(item_type, label, summary, source_episode_ids)
        )
        if not allowed:
            return False
        now = now_iso()
        target_list.append(
            {
                "id": generate_id("mem"),
                "type": item_type,
                "label": clip_text(label, MAX_LABEL),
                "summary": clip_text(summary, MAX_SUMMARY),
                "source_episode_ids": source_episode_ids,
                "confidence": self.long_term_policy.normalize_confidence(
                    str(operation.get("confidence", ""))
                ),
                "status": "active",
                "created_at": now,
                "updated_at": now,
            }
        )
        return True

    def _update_long_term_item(
        self,
        target_list: list[dict[str, Any]],
        operation: dict[str, Any],
        episode_ids: set[str],
        semantic: bool,
    ) -> bool:
        item = self._find_by_id(target_list, operation.get("target_id"))
        if item is None:
            return False
        summary = str(operation.get("summary", "")).strip()
        source_episode_ids = self._valid_source_episode_ids(operation.get("source_episode_ids"), episode_ids)
        item_type = str(item.get("type", ""))
        allowed = (
            self.long_term_policy.allows_semantic_item(item_type, item.get("label", ""), summary, source_episode_ids)
            if semantic
            else self.long_term_policy.allows_procedural_item(
                item_type,
                item.get("label", ""),
                summary,
                source_episode_ids,
            )
        )
        if not allowed:
            return False
        item["summary"] = clip_text(summary, MAX_SUMMARY)
        item["source_episode_ids"] = source_episode_ids
        item["confidence"] = self.long_term_policy.normalize_confidence(
            str(operation.get("confidence", item.get("confidence", "")))
        )
        item["status"] = "inactive" if operation.get("status") == "inactive" else "active"
        item["updated_at"] = now_iso()
        return True

    def _deactivate_long_term_item(
        self,
        target_list: list[dict[str, Any]],
        operation: dict[str, Any],
    ) -> bool:
        item = self._find_by_id(target_list, operation.get("target_id"))
        if item is None:
            return False
        item["status"] = "inactive"
        item["updated_at"] = now_iso()
        return True

    def _semantic_target(self, memory: dict[str, Any], item_type: Any) -> list[dict[str, Any]] | None:
        if item_type == "user_preference":
            return memory["semantic_memory"]["user_preferences"]
        if item_type == "project_note":
            return memory["semantic_memory"]["project_notes"]
        if item_type == "codebase_concept":
            return memory["semantic_memory"]["codebase_concepts"]
        return None

    def _valid_source_episode_ids(self, raw_ids: Any, episode_ids: set[str]) -> list[str]:
        return [
            value
            for value in normalize_string_list(raw_ids, 20, 120)
            if value in episode_ids
        ]

    def _episode_ids(self, memory: dict[str, Any]) -> set[str]:
        return {
            episode["id"]
            for episode in memory["episodic_memory"]["episodes"]
            if episode.get("id")
        }

    def _refresh_pending_episode_ids(self, memory: dict[str, Any]) -> None:
        memory["working_memory"]["pending_episode_ids"] = [
            episode["id"]
            for episode in memory["episodic_memory"]["episodes"]
            if episode.get("summary_status") == "pending"
        ]

    def _find_by_id(self, items: list[dict[str, Any]], item_id: Any) -> dict[str, Any] | None:
        target_id = str(item_id or "")
        return next((item for item in items if item.get("id") == target_id), None)
