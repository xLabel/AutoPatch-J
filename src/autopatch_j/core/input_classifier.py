from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autopatch_j.agent.llm_client import LLMCallPurpose, LLMClient
from autopatch_j.core.models import CodeScope, ConversationRoute, IntentType


INTENT_CLASSIFIER_PROMPT = (
    "你是 AutoPatch-J 的严格意图分类器。"
    "你只能返回以下英文标签之一，不要解释，不要加标点，不要输出其它内容："
    "code_audit, code_explain, general_chat, patch_explain, patch_revise。"
)

PATCH_ONLY_INTENTS = {IntentType.PATCH_EXPLAIN, IntentType.PATCH_REVISE}


def parse_llm_intent(raw_text: str) -> IntentType | None:
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


def build_llm_intent_classifier(llm: Any | None) -> Callable[[str, bool], IntentType | None] | None:
    if llm is None:
        return None

    def classify(user_text: str, has_pending_review: bool) -> IntentType | None:
        messages = [
            {
                "role": "system",
                "content": INTENT_CLASSIFIER_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"user_text: {user_text}\n"
                    f"has_pending_review: {str(has_pending_review).lower()}\n"
                    "分类规则：\n"
                    "- 用户要求检查、审查、扫描、发现代码问题：返回 code_audit。\n"
                    "- 用户要求解释代码、说明实现、讲清楚逻辑：返回 code_explain。\n"
                    "- 普通闲聊，或与代码任务无关：返回 general_chat。\n"
                    "- 当前存在待确认补丁，并且用户询问补丁原因、影响、风险：返回 patch_explain。\n"
                    "- 当前存在待确认补丁，并且用户要求修改、重做、调整补丁：返回 patch_revise。\n"
                    "如果 has_pending_review=false，不允许返回 patch_explain 或 patch_revise。"
                ),
            },
        ]
        intent = _classify_intent_with_purpose(llm, messages, LLMCallPurpose.CLASSIFIER)
        if intent is not None:
            return intent
        return _classify_intent_with_purpose(llm, messages, LLMCallPurpose.REACT)

    return classify


def _classify_intent_with_purpose(
    llm: Any,
    messages: list[dict[str, str]],
    purpose: LLMCallPurpose,
) -> IntentType | None:
    try:
        response = llm.chat(
            messages=messages,
            tools=None,
            purpose=purpose,
        )
    except Exception:
        return None
    return parse_llm_intent(str(response.content))


class IntentDetector:
    """
    用户输入的语义意图分类器。

    职责边界：
    1. 将自然语言输入归类为 code_audit、code_explain、general_chat、patch_explain 或 patch_revise。
    2. 使用短 LLM 做语义判断，并用程序硬约束过滤无 pending review 时的 patch-only 意图。
    3. 不决定 pending review 是否应继续；会话连续性由 ConversationRouter 判断。
    """

    def __init__(
        self,
        classify_with_llm: Callable[[str, bool], IntentType | None] | None = None,
    ) -> None:
        self.classify_with_llm = classify_with_llm

    def detect_intent(self, user_text: str, has_pending_review: bool) -> IntentType:
        llm_intent = self._fetch_llm_intent(user_text, has_pending_review)

        # 也可以在 has_pending_review=False 时动态裁掉 patch_explain/patch_revise，
        # 只把 code_audit/code_explain/general_chat 暴露给 LLM；但那会让分类协议
        # 随状态分叉，降低 prompt cache 命中和排查一致性。这里保持固定提示词，
        # 让 LLM 做语义分类，状态合法性由程序硬约束。
        if not has_pending_review and llm_intent in PATCH_ONLY_INTENTS:
            llm_intent = None

        if has_pending_review:
            return llm_intent or IntentType.PATCH_EXPLAIN
        return llm_intent or IntentType.GENERAL_CHAT

    def _fetch_llm_intent(self, user_text: str, has_pending_review: bool) -> IntentType | None:
        if self.classify_with_llm is None:
            return None
        try:
            return self.classify_with_llm(user_text, has_pending_review)
        except Exception:
            return None


@dataclass(slots=True)
class ConversationRouter:
    """
    pending review 场景下的会话连续性路由器。

    职责边界：
    1. 判断用户输入是继续当前补丁审核、发起新任务，还是系统命令。
    2. 优先使用显式信号（命令、@scope）切换路径，模糊输入再交给短 LLM。
    3. 不分类具体业务意图；route 确定后才由 IntentDetector 决定工作流类型。
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

        scope_summary = (
            ", ".join(current_scope.focus_files)
            if current_scope and current_scope.focus_files
            else "无"
        )
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
        route = self._fetch_route_with_purpose(messages, LLMCallPurpose.CLASSIFIER)
        if route is not None:
            return route
        return self._fetch_route_with_purpose(messages, LLMCallPurpose.REACT)

    def _fetch_route_with_purpose(
        self,
        messages: list[dict[str, str]],
        purpose: LLMCallPurpose,
    ) -> ConversationRoute | None:
        assert self.llm is not None
        try:
            response = self.llm.chat(
                messages=messages,
                tools=None,
                purpose=purpose,
            )
        except Exception:
            return None
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
