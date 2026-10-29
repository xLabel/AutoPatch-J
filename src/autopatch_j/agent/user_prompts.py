"""将用户原文、任务目标和补丁证据组织成独立提示章节。"""

from __future__ import annotations

from autopatch_j.core import prompting
from autopatch_j.core.domain import audit
from autopatch_j.core.domain import scope as domain_scope
from autopatch_j.core.domain import workspace


def build_code_audit_user_prompt(
    text: str,
    current_finding: audit.FindingTask,
    force_reread: bool,
) -> str:
    """分隔当前 finding、取证要求、有限重读要求和用户原文。"""
    sections: list[prompting.PromptSection] = [
        prompting.PromptSection(
            "当前目标",
            "系统已完成本地静态扫描。你当前只允许处理一个 finding，不"
            "要切换到其他目标。\n"
            f"- finding: {current_finding.finding_id}"
            f"\n"
            f"- 文件位置: {current_finding.file_path}"
            f":{current_finding.start_line}\n"
            f"- 规则 ID: {current_finding.check_id}"
            f"\n"
            f"- 问题描述: {current_finding.message}",
        ),
        prompting.PromptSection(
            "代码证据", f"```java\n{current_finding.snippet}\n```"
        ),
        prompting.PromptSection(
            "执行要求",
            f"1. 只处理 {current_finding.finding_id}"
            f"，不要切换到其他 F 编号。\n"
            "2. 优先根据 F 编号调用 get_finding_detai"
            "l 获取详情。\n"
            f"3. 如需漏洞详情，associated_finding_id "
            f"必须使用 {current_finding.finding_id}"
            f"。\n"
            f"4. 如需最新源代码，可读取 {current_finding.file_path}"
            f"。\n"
            f"5. 如形成补丁，propose_patch 时必须传 asso"
            f"ciated_finding_id={current_finding.finding_id}"
            f"。\n"
            "6. 如果你判断当前目标不值得修复，只输出一句短结论，不要展开长篇分析。",
        ),
    ]
    if force_reread:
        sections.append(
            prompting.PromptSection(
                "重试要求",
                "上一次 propose_patch 因 old_string 不匹配失败。\n"
                f"这一次你必须先用 read_source_context(pat"
                f"h={current_finding.file_path}, l"
                f"ine={current_finding.start_line}"
                f") "
                "或 read_source_block 重新确认源码，再重新 p"
                "ropose_patch。",
            )
        )
    sections.append(prompting.PromptSection("用户原始请求", text))
    return prompting.render_prompt_sections(*sections)


def build_zero_finding_review_user_prompt(text: str, file_path: str) -> str:
    """构造不依赖 F 句柄的单文件补漏请求。"""
    return prompting.render_prompt_sections(
        prompting.PromptSection(
            "当前目标",
            "系统已完成本地静态扫描，当前目标文件在本轮扫描中没有 finding。\n"
            f"- 当前目标文件: {file_path}",
        ),
        prompting.PromptSection(
            "执行要求",
            "1. 只围绕当前文件做一次轻量复核，不要假设存在 F 编号。\n"
            "2. 如需代码证据，只允许 read_source_file/r"
            "ead_source_context/read_source_b"
            "lock 读取当前文件。\n"
            "3. 只有在拿到具体代码证据、能明确指出风险并给出最小修法时，才"
            "允许 propose_patch。\n"
            "4. 如果没有明确证据支持修改，不要输出长篇分析。",
        ),
        prompting.PromptSection("用户原始请求", text),
    )


def build_code_explain_user_prompt(
    text: str,
    scope: domain_scope.CodeScope,
    project_context: str | None = None,
) -> str:
    """按单文件、目录或项目范围组织只读讲解证据。"""
    if scope.kind is domain_scope.CodeScopeKind.SINGLE_FILE:
        return prompting.render_prompt_sections(
            prompting.PromptSection(
                "解释范围", f"- 文件: {scope.focus_files[0]}"
            ),
            prompting.PromptSection(
                "回答要求",
                "- 只基于当前文件可见内容解释代码功能。\n"
                "- 不要主动搜索、读取或推断焦点范围外的类型实现、调用方或配置来源。\n"
                "- 如果出现外部类型名，只能基于当前文件里的使用方式做保守说明。\n"
                "- 回答默认控制在 2 到 4 句；除非用户明确要求详细展开，否"
                "则不要输出分节报告。",
            ),
            prompting.PromptSection("用户问题", text),
        )

    if scope.kind is domain_scope.CodeScopeKind.PROJECT:
        listed_files = "\n".join(f"- {path}" for path in scope.focus_files[:80])
        if len(scope.focus_files) > 80:
            listed_files += (
                f"\n- ... 其余 {len(scope.focus_files) - 80} 个 Java 文件已省略"
            )
        sections: list[prompting.PromptSection] = [
            prompting.PromptSection(
                "当前任务",
                "当前任务是项目级代码讲解。不要触发扫描，不要提出补丁。",
            ),
            prompting.PromptSection(
                "回答要求",
                "- 基于项目结构、索引和少量必要源码证据回答。\n"
                "- 默认控制在 3 到 6 行，优先说明项目用途推断、主要结构、"
                "关键类线索和判断依据。\n"
                "- 如果信息不足，必须说明只能根据当前文件结构和可见源码推断。\n"
                "- 最近扫描 ID（如 scan-xxxx）只是扫描记录，不是项"
                "目名称或项目描述。",
            ),
        ]
        if project_context:
            sections.append(
                prompting.PromptSection("项目上下文", project_context)
            )
        sections.extend(
            [
                prompting.PromptSection(
                    "项目 Java 文件清单", listed_files or "- 无 Java 文件"
                ),
                prompting.PromptSection("用户问题", text),
            ]
        )
        return prompting.render_prompt_sections(*sections)

    joined_paths = "\n".join(f"- {path}" for path in scope.focus_files)
    return prompting.render_prompt_sections(
        prompting.PromptSection("当前任务", "当前任务是代码讲解。"),
        prompting.PromptSection("当前焦点范围", joined_paths),
        prompting.PromptSection(
            "回答要求",
            "- 可以在当前焦点范围内使用 search_symbols 和源"
            "码读取工具辅助解释。\n"
            "- 不要越过当前 focus scope。\n"
            "- 回答默认控制在 1 段或 3 个要点以内；除非用户明确要求详"
            "细展开，否则不要输出长篇报告。",
        ),
        prompting.PromptSection("用户问题", text),
    )


def build_patch_explain_user_prompt(
    current_item: workspace.ReviewPatchItem, user_text: str
) -> str:
    """将当前 diff 与用户问题分隔，要求解释且避免重复粘贴差异。"""
    draft = current_item.draft
    return prompting.render_prompt_sections(
        prompting.PromptSection(
            "当前待确认补丁",
            f"- 文件: {current_item.file_path}\n"
            f"- 补丁意图: {draft.rationale or "无说明"}",
        ),
        prompting.PromptSection("补丁差异", f"```diff\n{draft.diff}\n```"),
        prompting.PromptSection(
            "回答要求",
            "1. 先直接回答用户问题。\n"
            "2. 默认只说明改了什么、为什么改、是否有明显风险。\n"
            "3. 不要重复粘贴补丁 diff。\n"
            "4. 不要输出长篇 Markdown 报告，除非用户明确要求。",
        ),
        prompting.PromptSection("用户问题", user_text),
    )


def build_patch_revise_user_prompt(
    current_item: workspace.ReviewPatchItem,
    user_text: str,
) -> str:
    """将当前草案、原 finding 关联与修订反馈分隔。"""
    draft = current_item.draft
    finding_handles = (
        ", ".join(current_item.finding_ids)
        if current_item.finding_ids
        else "无"
    )
    return prompting.render_prompt_sections(
        prompting.PromptSection(
            "当前待重写补丁",
            f"- 文件: {current_item.file_path}\n"
            f"- 关联 finding: {finding_handles}\n"
            f"- 补丁意图: {draft.rationale or "无说明"}",
        ),
        prompting.PromptSection("当前补丁差异", f"```diff\n{draft.diff}\n```"),
        prompting.PromptSection("用户反馈", user_text),
        prompting.PromptSection(
            "执行要求",
            "- 只重写当前补丁，不要修改、删除或重建后续补丁。\n"
            "- 如果当前补丁有关联 finding，revise_patch"
            " 的 associated_finding_id 必须保持当前关"
            "联，不要切换到其他 F 编号。\n"
            "- 如果用户反馈只是要求解释补丁，请直接回答，不要调用 revi"
            "se_patch。\n"
            "- 如果需要提交修订结果，必须调用 revise_patch。",
        ),
    )
