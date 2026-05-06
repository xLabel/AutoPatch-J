from __future__ import annotations

from enum import Enum


class IntentType(str, Enum):
    """自然语言输入可进入的业务意图集合。"""

    CODE_AUDIT = "code_audit"
    CODE_EXPLAIN = "code_explain"
    GENERAL_CHAT = "general_chat"
    PATCH_EXPLAIN = "patch_explain"
    PATCH_REVISE = "patch_revise"


class ConversationRoute(str, Enum):
    """pending review 场景下的会话路由结果。"""

    NEW_TASK = "new_task"
    REVIEW_CONTINUE = "review_continue"
    COMMAND = "command"
