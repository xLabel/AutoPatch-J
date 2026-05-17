from __future__ import annotations


PATCH_DRAFT_PARAMETERS = {
    "type": "object",
    "properties": {
        "file_path": {
            "type": "string",
            "description": "仓库内目标 Java 文件的相对路径，必须来自 finding 详情或源码读取工具结果。",
        },
        "old_string": {
            "type": "string",
            "description": "要替换的原始代码精确片段；调用前必须用 get_finding_detail、read_source_context、read_source_block 或 read_source_file 确认，必须和当前源码完全一致，不要省略缩进或上下文。",
        },
        "new_string": {
            "type": "string",
            "description": "替换后的完整代码片段，只包含 old_string 对应区域的新内容。",
        },
        "rationale": {
            "type": "string",
            "description": "简要说明为什么这样修复，以及修复依据来自哪个 finding 或源码证据。",
        },
        "associated_finding_id": {
            "type": "string",
            "description": "关联的 finding 句柄，如 F1。处理扫描 finding 时必须传入；无 finding 的轻量复核可省略。",
        },
    },
    "required": ["file_path", "old_string", "new_string", "rationale"],
}
