"""自然语言任务意图及审核会话路由枚举。"""

from __future__ import annotations

import enum


class IntentType(str, enum.Enum):
    """自然语言输入可进入的业务意图集合。"""

    CODE_AUDIT = "code_audit"
    CODE_EXPLAIN = "code_explain"
    GENERAL_CHAT = "general_chat"
    PATCH_EXPLAIN = "patch_explain"
    PATCH_REVISE = "patch_revise"


class ConversationRoute(str, enum.Enum):
    """pending review 场景下的会话路由结果。"""

    NEW_TASK = "new_task"
    REVIEW_CONTINUE = "review_continue"
    COMMAND = "command"
