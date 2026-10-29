"""面向普通问答的终端文本过滤，不改变持久消息。"""

from __future__ import annotations

import re

from autopatch_j.core.domain import intent as domain_intent


class ChatFilter:
    """CLI 最终回答的轻量格式过滤器。

    职责边界：
    1. 对 general_chat/code_explain 的最终回答做简单 Markdown 降噪。
    2. 保持控制台文本清爽，避免标题和粗体标记污染输出。
    3. 不做意图拦截，也不裁剪 Agent 过程；这些由 input classifier 和 AgentStreamPresenter 负责。
    """

    def build_display_answer(
        self,
        user_text: str,
        answer: str,
        intent: domain_intent.IntentType,
    ) -> str:
        """基础的去 Markdown 格式化，保持 CLI 干净。"""
        del user_text
        if intent not in {
            domain_intent.IntentType.GENERAL_CHAT,
            domain_intent.IntentType.CODE_EXPLAIN,
        }:
            return answer.strip()

        # 简单的去除 Markdown 大纲和加粗，保持控制台的纯文本清爽感
        normalized = answer.strip().replace("\r\n", "\n")
        normalized = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", normalized)
        normalized = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", normalized)
        normalized = normalized.replace("**", "")
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()
