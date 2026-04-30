from __future__ import annotations

import re

from autopatch_j.core.models import IntentType


class ChatFilter:
    """
    CLI 最终回答的轻量格式过滤器。

    职责边界：
    1. 对 general_chat/code_explain 的最终回答做简单 Markdown 降噪。
    2. 保持控制台文本清爽，避免标题和粗体标记污染输出。
    3. 不做意图拦截，也不裁剪 ReAct 过程；这些由 input classifier 和 StreamAdapter 负责。
    """

    def build_display_answer(
        self,
        user_text: str,
        answer: str,
        intent: IntentType,
    ) -> str:
        """基础的去 Markdown 格式化，保持 CLI 干净。"""
        if intent not in {IntentType.GENERAL_CHAT, IntentType.CODE_EXPLAIN}:
            return answer.strip()

        # 简单的去除 Markdown 大纲和加粗，保持控制台的纯文本清爽感
        normalized = answer.strip().replace("\r\n", "\n")
        normalized = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", normalized)
        normalized = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", "", normalized)
        normalized = normalized.replace("**", "")
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        return normalized.strip()
