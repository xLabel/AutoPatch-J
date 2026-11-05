"""模型可见的 function tool 名称，独立于 Python 方法命名。"""

import enum


class FunctionToolName(enum.StrEnum):
    """项目 function tools 及官方 Memory 的公开名称。"""

    START_REPAIR = "start_repair"
    REQUEST_WRITE_SCOPE = "request_write_scope"
    RECORD_CONSTRAINT = "record_constraint"
    SCAN = "scan_java"
    GET_SCAN_FINDINGS = "get_scan_findings"
    GET_FINDING_DETAIL = "get_finding_detail"
    READ_SOURCE_FILE = "read_source_file"
    READ_SOURCE_BLOCK = "read_source_block"
    READ_SOURCE_CONTEXT = "read_source_context"
    SEARCH_SYMBOLS = "search_symbols"
    GET_PROPOSAL = "get_proposal"
    ASSESS_ISSUE = "assess_issue"
    PROPOSE_PATCH = "propose_patch"
    MEMORY_SEARCH = "search_memory"
    MEMORY_READ = "read_memory"
    MEMORY_WRITE = "write_memory"
    MEMORY_DELETE = "delete_memory"
