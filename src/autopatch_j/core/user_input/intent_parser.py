from __future__ import annotations

import re

from autopatch_j.core.domain.intent import IntentType


def parse_intent_label(raw_text: str) -> IntentType | None:
    normalized = raw_text.strip().lower()
    if not normalized:
        return None

    normalized = re.sub(r"```[a-zA-Z0-9_-]*", "", normalized).replace("```", "")
    labels: dict[str, IntentType] = {}
    for intent in IntentType:
        labels[intent.value] = intent
        labels[intent.name.lower()] = intent
        labels[intent.value.replace("_", "")] = intent

    labels.update(
        {
            "代码审查": IntentType.CODE_AUDIT,
            "代码检查": IntentType.CODE_AUDIT,
            "代码解释": IntentType.CODE_EXPLAIN,
            "普通聊天": IntentType.GENERAL_CHAT,
            "补丁解释": IntentType.PATCH_EXPLAIN,
            "补丁修改": IntentType.PATCH_REVISE,
            "修改补丁": IntentType.PATCH_REVISE,
        }
    )

    found: set[IntentType] = set()
    for label, intent in labels.items():
        if re.search(rf"(?<![a-z0-9_]){re.escape(label)}(?![a-z0-9_])", normalized):
            found.add(intent)

    compact = re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", normalized)
    if compact in labels:
        found.add(labels[compact])

    if len(found) == 1:
        return next(iter(found))
    return None
