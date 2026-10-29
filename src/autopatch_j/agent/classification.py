"""短分类调用的 SDK 适配；业务默认值留在输入路由层。"""

from collections.abc import Callable

import openai
import pydantic_ai
from pydantic_ai import exceptions
from pydantic_ai import usage

from autopatch_j.core.domain import intent
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.user_input import intent_parser
from autopatch_j.core.user_input import prompts
from autopatch_j.llm import policy
from autopatch_j.llm import runtime


def _complete(
    model_runtime: runtime.ModelRuntime, system: str, prompt: str
) -> tuple[str, str]:
    """执行一次非流式短分类；预期模型失败返回原因，取消保持中断。"""
    if model_runtime.model is None:
        return "", "未配置模型"
    settings = policy.request_settings(model_runtime.profile, classifier=True)
    agent: pydantic_ai.Agent[None, str] = pydantic_ai.Agent(
        model_runtime.model,
        instructions=system,
        deps_type=type(None),
        retries=pydantic_ai.AgentRetries(output=0),
        capabilities=[policy.FinishGuard()],
        model_settings=settings,
    )
    try:
        response = model_runtime.run(
            agent.run(prompt, usage_limits=usage.UsageLimits(request_limit=1))
        )
    except (
        exceptions.AgentRunError,
        openai.APIError,
        policy.ModelFailure,
    ) as error:
        return "", type(error).__name__
    return response.output, ""


def build_intent_classifier(
    model_runtime: runtime.ModelRuntime,
) -> Callable[[str, bool], tuple[intent.IntentType | None, str]]:
    """构造只做一次调用的意图分类函数。"""

    def classify(
        text: str, pending: bool
    ) -> tuple[intent.IntentType | None, str]:
        """执行一次模型分类；预期失败返回 None，由业务层选择默认意图。"""
        answer, error = _complete(
            model_runtime,
            prompts.INTENT_CLASSIFIER_PROMPT,
            prompts.build_intent_classifier_user_prompt(text, pending),
        )
        return intent_parser.parse_intent_label(answer), error

    return classify


def build_route_classifier(
    model_runtime: runtime.ModelRuntime,
) -> Callable[
    [str, str | None, domain_scope.CodeScope | None],
    tuple[intent.ConversationRoute | None, str],
]:
    """构造 pending review 的一次短路由调用。"""

    def classify(
        text: str, file: str | None, scope: domain_scope.CodeScope | None
    ) -> tuple[intent.ConversationRoute | None, str]:
        """执行一次模型分类；预期失败返回 None，由业务层选择默认意图。"""
        paths = ", ".join(scope.focus_files) if scope else "无"
        answer, error = _complete(
            model_runtime,
            prompts.REVIEW_ROUTE_CLASSIFIER_PROMPT,
            prompts.build_review_route_user_prompt(text, file, paths),
        )
        normalized = "".join(char for char in answer.upper() if char.isalpha())
        for route in intent.ConversationRoute:
            if route.value.replace("_", "").upper() == normalized:
                return route, error
        return None, error or "分类返回了无效路由"

    return classify
