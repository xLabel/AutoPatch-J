from __future__ import annotations

import re

from autopatch_j.core.models import IntentType


class ChatFilter:
    """
    输出内容治理服务 (Output Governance)。
    核心职责：对 LLM 冗长的回答进行物理裁切，避免大段 Markdown 破坏 CLI 体验。
    不再包含硬编码的意图拦截逻辑，拦截闲聊职责已完全交由大模型的 System Prompt 处理。
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
