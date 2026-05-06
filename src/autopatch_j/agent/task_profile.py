from __future__ import annotations

from dataclasses import dataclass

from autopatch_j.core.models import IntentType


@dataclass(frozen=True, slots=True)
class TaskProfile:
    """
    Agent 任务的静态执行边界。

    intent 决定系统提示词，tool_names 决定本轮 ReAct 可调用的工具集合。
    它只描述任务配置，不保存运行时状态。
    """

    intent: IntentType
    tool_names: tuple[str, ...]


TASK_PROFILES: dict[IntentType, TaskProfile] = {
    IntentType.CODE_AUDIT: TaskProfile(
        intent=IntentType.CODE_AUDIT,
        tool_names=(
            "get_finding_detail",
            "read_source_code",
            "propose_patch",
        ),
    ),
    IntentType.CODE_EXPLAIN: TaskProfile(
        intent=IntentType.CODE_EXPLAIN,
        tool_names=(
            "search_symbols",
            "read_source_code",
        ),
    ),
    IntentType.GENERAL_CHAT: TaskProfile(
        intent=IntentType.GENERAL_CHAT,
        tool_names=(),
    ),
    IntentType.PATCH_EXPLAIN: TaskProfile(
        intent=IntentType.PATCH_EXPLAIN,
        tool_names=(
            "search_symbols",
            "read_source_code",
        ),
    ),
    IntentType.PATCH_REVISE: TaskProfile(
        intent=IntentType.PATCH_REVISE,
        tool_names=(
            "search_symbols",
            "read_source_code",
            "get_finding_detail",
            "revise_patch",
        ),
    ),
}

CODE_EXPLAIN_SINGLE_FILE_PROFILE = TaskProfile(
    intent=IntentType.CODE_EXPLAIN,
    tool_names=("read_source_code",),
)
ZERO_FINDING_REVIEW_PROFILE = TaskProfile(
    intent=IntentType.CODE_AUDIT,
    tool_names=(
        "read_source_code",
        "propose_patch",
    ),
)


def fetch_task_profile(intent: IntentType) -> TaskProfile:
    return TASK_PROFILES[intent]


def fetch_code_explain_profile(allow_symbol_search: bool) -> TaskProfile:
    if allow_symbol_search:
        return TASK_PROFILES[IntentType.CODE_EXPLAIN]
    return CODE_EXPLAIN_SINGLE_FILE_PROFILE
