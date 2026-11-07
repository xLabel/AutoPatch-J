"""从显式项目配置装配 SDK；此处不发送模型请求。"""

import asyncio
import os

import openai
from pydantic_ai.models import openai as openai_model
from pydantic_ai.profiles import openai as openai_profile
from pydantic_ai.providers import openai as openai_provider

from autopatch_j import config
from autopatch_j import error_messages
from autopatch_j.llm import runtime


def build_model_runtime(
    app_config: config.AppConfig,
) -> runtime.ModelRuntime:
    """配置完整有效才装配模型；失败不创建本地替代运行时。"""
    # 任意次生客户端关闭故障都只补充到原装配异常。
    # pylint: disable=broad-exception-caught
    os.environ["PYDANTIC_AI_NO_BANNER"] = "1"
    profile = app_config.resolve_llm_context_profile()
    reason = app_config.llm_unavailable_reason()
    if reason:
        raise ValueError(reason)
    client = openai.AsyncOpenAI(
        api_key=app_config.llm_api_key,
        base_url=app_config.llm_base_url,
    )
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
        return runtime.ModelRuntime(model, profile, client)
    except BaseException as exc:
        # 收尾故障不能覆盖装配阶段的原始失败。
        try:
            with asyncio.Runner() as runner:
                runner.run(client.close())
        except (
            Exception
        ) as close_error:  # pylint: disable=broad-exception-caught
            exc.add_note(
                error_messages.failed("模型客户端关闭", str(close_error))
            )
        raise
