"""模型容量配置；实际请求容量由服务端判断。"""

import pytest

from autopatch_j.llm import context_window


def test_deepseek_capacity_uses_official_window_and_application_budget():
    profile = context_window.resolve_context_profile(model="deepseek-flash")
    assert profile.context_window == 1_048_576
    assert profile.provider_max_output_tokens == 393_216
    assert profile.input_capacity == 887_808
    assert profile.max_output_tokens == 128_000


@pytest.mark.parametrize(
    "model",
    ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash", "custom"],
)
def test_unknown_models_require_explicit_capacities(model):
    with pytest.raises(ValueError):
        context_window.resolve_context_profile(model=model)
