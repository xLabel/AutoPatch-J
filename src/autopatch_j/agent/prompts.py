from __future__ import annotations

from autopatch_j.core.models import IntentType

BASE_SYSTEM_PROMPT = """你是 AutoPatch-J 的代码修复智能体。
你的首要目标是做出可验证、最小化、工程化的判断与补丁提案。
你必须遵守当前任务类型、工具白名单和焦点文件约束。"""

TASK_PROMPTS: dict[IntentType, str] = {
    IntentType.CODE_AUDIT: (
        "当前任务是 code_audit。请围绕已经给定的扫描结果做甄别、取证和补丁提案。"
        "默认不要重新扫描项目，除非调用方明确把 scan_project 开放给你。"
        "如果调用方已经在用户消息中提供 F 编号摘要，你应优先基于这些 F 编号调用 get_finding_detail，"
        "不要先做无关的代码讲解，也不要搜索焦点范围之外的符号。"
    ),
    IntentType.CODE_EXPLAIN: (
        "当前任务是 code_explain。你的职责是解释代码，不做扫描，不提出补丁。"
        "默认用 2 到 4 句纯文本短答，不要输出 Markdown 标题、教程式大纲或长篇报告。"
    ),
    IntentType.GENERAL_CHAT: (
        "当前任务是 general_chat。请只回答编程、修复、架构、算法、工具和项目相关问题，"
        "不要回答生活类或泛百科问题。默认用 1 到 3 段纯文本短答，不要输出 Markdown 标题、"
        "教程式编号大纲或长篇模板化内容。"
    ),
    IntentType.PATCH_EXPLAIN: (
        "当前任务是 patch_explain。请解释当前待审核补丁的意图、风险和影响，只读回答。"
    ),
    IntentType.PATCH_REVISE: (
        "当前任务是 patch_revise。请围绕当前待审核补丁和用户反馈重写剩余补丁方案。"
        "你可以读代码、查找符号、取回漏洞详情并重新 propose_patch。"
    ),
}


def build_workbench_prompt(
    pending_file: str | None,
    last_scan: str | None,
    focus_paths: list[str] | None = None,
) -> str:
    lines = [
        "## 当前工作台",
        f"- 最近扫描: {last_scan or '尚未扫描'}",
        f"- 待审核补丁: {pending_file or '无'}",
    ]
    if focus_paths:
        lines.append(f"- 焦点文件: {', '.join(focus_paths)}")
        lines.append("- 严禁扫描、读取或修复焦点范围之外的路径。")
    return "\n".join(lines)


def build_task_system_prompt(
    intent: IntentType,
    pending_file: str | None,
    last_scan: str | None,
    focus_paths: list[str] | None = None,
) -> str:
    return "\n\n".join(
        [
            BASE_SYSTEM_PROMPT,
            TASK_PROMPTS[intent],
            build_workbench_prompt(
                pending_file=pending_file,
                last_scan=last_scan,
                focus_paths=focus_paths,
            ),
        ]
    )


def build_legacy_system_prompt(
    pending_file: str | None,
    last_scan: str | None,
    focus_paths: list[str] | None = None,
) -> str:
    legacy_prompt = (
        "当前处于 legacy_chat 兼容模式。你可以自主决定是否扫描、取证、读代码和提补丁，"
        "但仍必须遵守焦点文件约束，并避免无意义的重复扫描。"
    )
    return "\n\n".join(
        [
            BASE_SYSTEM_PROMPT,
            legacy_prompt,
            build_workbench_prompt(
                pending_file=pending_file,
                last_scan=last_scan,
                focus_paths=focus_paths,
            ),
        ]
    )
