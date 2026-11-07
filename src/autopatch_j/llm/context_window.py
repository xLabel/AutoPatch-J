"""模型官方容量与应用操作预算，不宣称精确 token 计数。"""

import dataclasses

from autopatch_j import error_messages


@dataclasses.dataclass(frozen=True, slots=True)
class ModelContextProfile:
    """模型容量与一次操作的输出及安全预算。"""

    model: str
    context_window: int
    provider_max_output_tokens: int
    max_output_tokens: int = 128_000
    provider_safety_tokens: int = 32_768

    @property
    def input_capacity(self) -> int:
        """扣除输出预算和协议安全余量后的输入限值。"""
        return (
            self.context_window
            - self.max_output_tokens
            - self.provider_safety_tokens
        )


def resolve_context_profile(
    *,
    model: str,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
) -> ModelContextProfile:
    """已知模型使用官方容量，未知部署必须明确给出容量。"""
    if model.strip() in {"deepseek-flash", "deepseek-v4-pro"}:
        if context_window is not None or max_output_tokens is not None:
            raise ValueError(error_messages.FIXED_MODEL_CAPACITY)
        return ModelContextProfile(model, 1_048_576, 393_216)
    if context_window is None or max_output_tokens is None:
        raise ValueError(error_messages.UNKNOWN_MODEL_CAPACITY)
    profile = ModelContextProfile(
        model, context_window, max_output_tokens, max_output_tokens
    )
    if max_output_tokens <= 0 or profile.input_capacity <= 0:
        raise ValueError(error_messages.INSUFFICIENT_CAPACITY)
    return profile
