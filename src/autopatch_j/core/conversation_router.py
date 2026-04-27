from __future__ import annotations

import re
from dataclasses import dataclass

from autopatch_j.agent.llm_client import LLMClient
from autopatch_j.core.models import CodeScope, ConversationRoute


@dataclass(slots=True)
class ConversationRouter:
    """
    会话连续性判定服务 (Core Service)
    职责：判断当前输入是新任务、当前审核的继续操作，还是命令。
    """

    llm: LLMClient | None = None

    def determine_route(
        self,
        user_text: str,
        has_pending_review: bool,
        requested_scope: CodeScope | None,
        current_patch_file: str | None,
        current_scope: CodeScope | None,
    ) -> ConversationRoute:
        stripped = user_text.strip()
        if stripped.startswith("/"):
            return ConversationRoute.COMMAND
        if not has_pending_review:
            return ConversationRoute.NEW_TASK
        if "@" in user_text or requested_scope is not None:
            return ConversationRoute.NEW_TASK

        llm_route = self._fetch_route_by_llm(
            user_text=user_text,
            current_patch_file=current_patch_file,
            current_scope=current_scope,
        )
        return llm_route or ConversationRoute.REVIEW_CONTINUE

    def _fetch_route_by_llm(
        self,
        user_text: str,
        current_patch_file: str | None,
        current_scope: CodeScope | None,
    ) -> ConversationRoute | None:
        if self.llm is None:
            return None

        scope_summary = ", ".join(current_scope.focus_files) if current_scope and current_scope.focus_files else "无"
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个严格的会话路由分类器。"
                    "你只能返回以下三个标签之一：NEW_TASK、REVIEW_CONTINUE、COMMAND。"
                    "不要输出任何解释、标点或额外文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户输入：{user_text}\n"
                    f"当前待审核补丁文件：{current_patch_file or '无'}\n"
                    f"当前工作范围：{scope_summary}\n"
                    "判定标准：\n"
                    "1. 如果用户是在发起新的代码任务（重新检查、扫描、修复、解释代码，或重新指定代码范围），返回 NEW_TASK。\n"
                    "2. 如果用户是在继续当前补丁审核（解释补丁、要求修改补丁），返回 REVIEW_CONTINUE。\n"
                    "3. 如果用户输入的是命令，返回 COMMAND。"
                ),
            },
        ]
        response = self.llm.chat(messages=messages, tools=None)
        return self._parse_route(str(response.content))

    def _parse_route(self, raw_text: str) -> ConversationRoute | None:
        normalized = re.sub(r"[^A-Z_]", "", raw_text.upper())
        compact = normalized.replace("_", "")
        if "NEWTASK" in compact:
            return ConversationRoute.NEW_TASK
        if "REVIEWCONTINUE" in compact:
            return ConversationRoute.REVIEW_CONTINUE
        if "COMMAND" in compact:
            return ConversationRoute.COMMAND
        return None
