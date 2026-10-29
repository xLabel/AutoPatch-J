"""从有界分类文本识别唯一合法任务标签。"""

from __future__ import annotations

import re

from autopatch_j.core.domain import intent as domain_intent


def parse_intent_label(raw_text: str) -> domain_intent.IntentType | None:
    """只接受唯一合法意图标签；无效或多标签返回 None。"""
    normalized = raw_text.strip().lower()
    if not normalized:
        return None

    normalized = re.sub(r"```[a-zA-Z0-9_-]*", "", normalized).replace("```", "")
    labels: dict[str, domain_intent.IntentType] = {}
    for intent in domain_intent.IntentType:
        labels[intent.value] = intent
        labels[intent.name.lower()] = intent
        labels[intent.value.replace("_", "")] = intent

    labels.update(
        {
            "代码审查": domain_intent.IntentType.CODE_AUDIT,
            "代码检查": domain_intent.IntentType.CODE_AUDIT,
            "代码解释": domain_intent.IntentType.CODE_EXPLAIN,
            "普通聊天": domain_intent.IntentType.GENERAL_CHAT,
            "补丁解释": domain_intent.IntentType.PATCH_EXPLAIN,
            "补丁修改": domain_intent.IntentType.PATCH_REVISE,
            "修改补丁": domain_intent.IntentType.PATCH_REVISE,
        }
    )

    found: set[domain_intent.IntentType] = set()
    for label, intent in labels.items():
        if re.search(
            rf"(?<![a-z0-9_]){re.escape(label)}(?![a-z0-9_])", normalized
        ):
            found.add(intent)

    compact = re.sub(r"[^a-z0-9_\u4e00-\u9fff]", "", normalized)
    if compact in labels:
        found.add(labels[compact])

    if len(found) == 1:
        return next(iter(found))
    return None
