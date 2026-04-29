from __future__ import annotations

from autopatch_j.core.models import IntentType, AuditFindingItem, CodeScope, CodeScopeKind, PatchReviewItem

BASE_SYSTEM_PROMPT = """你是 AutoPatch-J 的代码修复智能体。
你的首要目标是做出可验证、最小化、工程化的判断与补丁提案。
你必须遵守当前任务类型、工具白名单和焦点文件约束。"""

TASK_PROMPTS: dict[IntentType, str] = {
    IntentType.CODE_AUDIT: (
        "当前任务是 code_audit。请围绕已经给定的扫描结果做甄别、取证和补丁提案。"
        "扫描已由本地 workflow 执行，默认不要重新扫描。"
        "如果调用方已经在用户消息中提供 F 编号摘要，你应优先基于这些 F 编号调用 get_finding_detail。"
        "不要先做无关的代码讲解，也不要搜索焦点范围之外的符号。"
    ),
    IntentType.CODE_EXPLAIN: (
        "当前任务是 code_explain。你的职责是解释代码，不做扫描，不提出补丁。"
        "默认用 2 到 4 句纯文本短答，不要输出 Markdown 标题、教程式大纲或长篇报告。"
    ),
    IntentType.GENERAL_CHAT: (
        "当前任务是 general_chat。你是一个严谨的 Java 开发专家。"
        "如果用户询问与编程、代码库、架构或软件工程完全无关的话题，请委婉但坚决地拒绝回答（例如明确告知'我只处理代码与工程相关问题'）。"
        "默认用 1 到 3 段纯文本短答，不要输出 Markdown 标题、教程式编号大纲或长篇模板化内容。"
    ),
    IntentType.PATCH_EXPLAIN: (
        "当前任务是 patch_explain。请解释当前待确认补丁的意图、风险和影响，只读回答。"
    ),
    IntentType.PATCH_REVISE: (
        "当前任务是 patch_revise。请围绕当前待确认补丁和用户反馈重写剩余补丁方案。"
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
        f"- 待确认补丁: {pending_file or '无'}",
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


def build_zero_finding_review_system_prompt(
    last_scan: str | None,
    focus_paths: list[str] | None = None,
) -> str:
    return "\n\n".join(
        [
            BASE_SYSTEM_PROMPT,
            (
                "当前任务是 zero_finding_review。静态扫描器在当前范围内未发现 finding，"
                "你需要做一次轻量复核来补漏。"
                "不要假设存在 F1/F2，也不要调用 get_finding_detail。"
                "只有在你拿到具体代码证据、能明确指出风险并给出最小修法时，才允许 propose_patch。"
                "如果没有明确证据支持修改，请保持简洁，不要展开长篇分析。"
            ),
            build_workbench_prompt(
                pending_file=None,
                last_scan=last_scan,
                focus_paths=focus_paths,
            ),
        ]
    )

def build_code_audit_user_prompt(
    text: str,
    current_finding: AuditFindingItem,
    force_reread: bool,
) -> str:
    lines = [
        "系统已完成本地静态扫描。你当前只允许处理一个 finding，不要切换到其他目标。",
        f"当前目标: {current_finding.finding_id}",
        f"文件位置: {current_finding.file_path}:{current_finding.start_line}",
        f"规则 ID: {current_finding.check_id}",
        f"问题描述: {current_finding.message}",
        f"代码证据:\n```java\n{current_finding.snippet}\n```",
        "",
        "执行要求:",
        f"1. 只处理 {current_finding.finding_id}，不要切换到其他 F 编号。",
        "2. 优先根据 F 编号调用 get_finding_detail 获取详情。",
        f"3. 如需漏洞详情，associated_finding_id 必须使用 {current_finding.finding_id}。",
        f"4. 如需最新源代码，可读取 {current_finding.file_path}。",
        f"5. 如形成补丁，propose_patch 时必须传 associated_finding_id={current_finding.finding_id}。",
        "6. 如果你判断当前目标不值得修复，只输出一句短结论，不要展开长篇分析。",
    ]
    if force_reread:
        lines.extend(
            [
                "",
                "上一次 propose_patch 因 old_string 不匹配失败。",
                f"这一次你必须先 read_source_code({current_finding.file_path})，再重新 propose_patch。",
            ]
        )
    lines.extend(["", f"用户原始请求: {text}"])
    return "\n".join(lines)

def build_zero_finding_review_user_prompt(text: str, file_path: str) -> str:
    return "\n".join(
        [
            "系统已完成本地静态扫描，当前目标文件在本轮扫描中没有 finding。",
            f"当前目标文件: {file_path}",
            "执行要求:",
            "1. 只围绕当前文件做一次轻量复核，不要假设存在 F 编号。",
            "2. 如需代码证据，只允许 read_source_code 当前文件。",
            "3. 只有在拿到具体代码证据、能明确指出风险并给出最小修法时，才允许 propose_patch。",
            "4. 如果没有明确证据支持修改，不要输出长篇分析。",
            "",
            f"用户原始请求: {text}",
        ]
    )

def build_code_explain_user_prompt(text: str, scope: CodeScope) -> str:
    if scope.kind is CodeScopeKind.SINGLE_FILE:
        return (
            f"当前解释范围仅限文件: {scope.focus_files[0]}\n"
            "请只基于当前文件可见内容解释代码功能，不要主动搜索、读取或推断焦点范围外的类型实现、调用方或配置来源。"
            "如果出现外部类型名，只能基于当前文件里的使用方式做保守说明。"
            "回答默认控制在 2 到 4 句；除非用户明确要求详细展开，否则不要输出分节报告。\n\n"
            f"用户问题:\n{text}"
        )

    joined_paths = "\n".join(f"- {path}" for path in scope.focus_files)
    return (
        "当前任务是代码讲解。你可以在当前焦点范围内使用 search_symbols 和 read_source_code 辅助解释，"
        "但不要越过当前 focus scope。回答默认控制在 1 段或 3 个要点以内；"
        "除非用户明确要求详细展开，否则不要输出长篇报告。\n"
        f"当前焦点范围:\n{joined_paths}\n\n"
        f"用户问题:\n{text}"
    )

def build_patch_explain_user_prompt(current_item: PatchReviewItem, user_text: str) -> str:
    draft = current_item.draft
    return (
        f"当前待确认补丁文件: {current_item.file_path}\n"
        f"补丁意图: {draft.rationale or '无说明'}\n"
        f"补丁差异:\n{draft.diff}\n\n"
        f"用户问题:\n{user_text}"
    )

def build_patch_revise_user_prompt(
    current_item: PatchReviewItem,
    remaining_items: list[PatchReviewItem],
    user_text: str,
) -> str:
    draft = current_item.draft
    remaining_files = "\n".join(f"- {item.file_path}" for item in remaining_items)
    return (
        f"当前待重写补丁文件: {current_item.file_path}\n"
        f"当前补丁意图: {draft.rationale or '无说明'}\n"
        f"当前补丁差异:\n{draft.diff}\n\n"
        "以下补丁尾部已失效，需要基于用户反馈整体重建:\n"
        f"{remaining_files}\n\n"
        f"用户反馈:\n{user_text}\n"
        "请基于最新意见重新生成 remaining_patch_items。"
    )
