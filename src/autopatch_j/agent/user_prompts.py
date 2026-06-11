from __future__ import annotations

from autopatch_j.core.domain import CodeScope, CodeScopeKind, FindingTask, ReviewPatchItem


def build_code_audit_user_prompt(
    text: str,
    current_finding: FindingTask,
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
                f"这一次你必须先用 read_source_context(path={current_finding.file_path}, line={current_finding.start_line}) 或 read_source_block 重新确认源码，再重新 propose_patch。",
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
            "2. 如需代码证据，只允许 read_source_file/read_source_context/read_source_block 读取当前文件。",
            "3. 只有在拿到具体代码证据、能明确指出风险并给出最小修法时，才允许 propose_patch。",
            "4. 如果没有明确证据支持修改，不要输出长篇分析。",
            "",
            f"用户原始请求: {text}",
        ]
    )


def build_code_explain_user_prompt(
    text: str,
    scope: CodeScope,
    project_context: str | None = None,
) -> str:
    if scope.kind is CodeScopeKind.SINGLE_FILE:
        return (
            f"当前解释范围仅限文件: {scope.focus_files[0]}\n"
            "请只基于当前文件可见内容解释代码功能，不要主动搜索、读取或推断焦点范围外的类型实现、调用方或配置来源。"
            "如果出现外部类型名，只能基于当前文件里的使用方式做保守说明。"
            "回答默认控制在 2 到 4 句；除非用户明确要求详细展开，否则不要输出分节报告。\n\n"
            f"用户问题:\n{text}"
        )

    if scope.kind is CodeScopeKind.PROJECT:
        listed_files = "\n".join(f"- {path}" for path in scope.focus_files[:80])
        if len(scope.focus_files) > 80:
            listed_files += f"\n- ... 其余 {len(scope.focus_files) - 80} 个 Java 文件已省略"
        return (
            "当前任务是项目级代码讲解。请基于项目结构、索引和少量必要源码证据回答，不要触发扫描，不要提出补丁。"
            "回答默认控制在 3 到 6 行，优先说明项目用途推断、主要结构、关键类线索和判断依据。"
            "如果信息不足，必须说明只能根据当前文件结构和可见源码推断。"
            "最近扫描 ID（如 scan-xxxx）只是扫描记录，不是项目名称或项目描述。\n\n"
            f"{project_context or ''}\n"
            f"项目 Java 文件清单:\n{listed_files or '- 无 Java 文件'}\n\n"
            f"用户问题:\n{text}"
        )

    joined_paths = "\n".join(f"- {path}" for path in scope.focus_files)
    return (
        "当前任务是代码讲解。你可以在当前焦点范围内使用 search_symbols 和源码读取工具辅助解释，"
        "但不要越过当前 focus scope。回答默认控制在 1 段或 3 个要点以内；"
        "除非用户明确要求详细展开，否则不要输出长篇报告。\n"
        f"当前焦点范围:\n{joined_paths}\n\n"
        f"用户问题:\n{text}"
    )


def build_patch_explain_user_prompt(current_item: ReviewPatchItem, user_text: str) -> str:
    draft = current_item.draft
    return (
        f"当前待确认补丁文件: {current_item.file_path}\n"
        f"补丁意图: {draft.rationale or '无说明'}\n"
        f"补丁差异:\n{draft.diff}\n\n"
        "回答要求:\n"
        "1. 先直接回答用户问题。\n"
        "2. 默认只说明改了什么、为什么改、是否有明显风险。\n"
        "3. 不要重复粘贴补丁 diff。\n"
        "4. 不要输出长篇 Markdown 报告，除非用户明确要求。\n\n"
        f"用户问题:\n{user_text}"
    )


def build_patch_revise_user_prompt(
    current_item: ReviewPatchItem,
    user_text: str,
) -> str:
    draft = current_item.draft
    finding_handles = ", ".join(current_item.finding_ids) if current_item.finding_ids else "无"
    return (
        f"当前待重写补丁文件: {current_item.file_path}\n"
        f"当前补丁关联 finding: {finding_handles}\n"
        f"当前补丁意图: {draft.rationale or '无说明'}\n"
        f"当前补丁差异:\n{draft.diff}\n\n"
        f"用户反馈:\n{user_text}\n"
        "请只重写当前补丁，不要修改、删除或重建后续补丁。"
        "如果当前补丁有关联 finding，revise_patch 的 associated_finding_id 必须保持当前关联，不要切换到其他 F 编号。"
        "如果用户反馈只是要求解释补丁，请直接回答，不要调用 revise_patch。"
        "如果需要提交修订结果，必须调用 revise_patch。"
    )
