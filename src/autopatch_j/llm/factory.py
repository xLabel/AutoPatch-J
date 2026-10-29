"""从显式项目配置装配 SDK；此处不发送模型请求。"""

import contextlib
import os

import openai
from pydantic_ai.models import openai as openai_model
from pydantic_ai.profiles import openai as openai_profile
from pydantic_ai.providers import openai as openai_provider

from autopatch_j import config
from autopatch_j.llm import policy
from autopatch_j.llm import runtime


def build_model_runtime(
    app_config: config.AppConfig | None = None,
) -> runtime.ModelRuntime:
    """无 Key 时仍构造本地资源，模型操作再提示缺少配置。"""
    os.environ["PYDANTIC_AI_NO_BANNER"] = "1"
    app_config = app_config or config.GLOBAL_CONFIG
    profile = app_config.resolve_llm_context_profile()
    if not app_config.is_llm_ready():
        return runtime.ModelRuntime(None, profile)
    client = openai.AsyncOpenAI(
        api_key=app_config.llm_api_key,
        base_url=app_config.llm_base_url,
        max_retries=2,
    )
    resources = runtime.ModelRuntime(None, profile, client)
    try:
        model = openai_model.OpenAIChatModel(
            app_config.llm_model,
            provider=openai_provider.OpenAIProvider(openai_client=client),
            profile=openai_profile.OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=app_config.llm_model
                not in {"deepseek-flash", "deepseek-v4-pro"},
                openai_chat_streaming_requires_finish_reason=True,
            ),
        )
        resources.model = policy.CapacityGuard(model, profile)
        return resources
    except BaseException:
        # 收尾故障不能覆盖装配阶段的原始失败。
        with contextlib.suppress(Exception):
            resources.close()
        raise
