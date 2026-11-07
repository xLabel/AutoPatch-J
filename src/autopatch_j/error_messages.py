"""统一错误文案；只组织文字，不依赖业务、SDK 或终端组件。"""

API_KEY_REQUIRED = "请设置 AUTOPATCH_LLM_API_KEY 后重启。"
INVALID_BASE_URL = "AUTOPATCH_LLM_BASE_URL 必须是有效的 HTTP(S) 地址。"
EMPTY_MODEL = "AUTOPATCH_LLM_MODEL 不能为空。"
UNKNOWN_MODEL_CAPACITY = (
    "该模型须设置 AUTOPATCH_LLM_CONTEXT_WINDOW 和 "
    "AUTOPATCH_LLM_MAX_OUTPUT_TOKENS。"
)
FIXED_MODEL_CAPACITY = "当前模型使用内置容量，请移除容量覆盖配置。"
INSUFFICIENT_CAPACITY = "上下文容量不足，请检查输入、输出容量配置。"
RUNTIME_CLOSED = "项目服务已关闭，请执行 /init。"
REVIEW_REQUIRED = "当前没有有效的审核画面，请查看后重新输入操作。"
STATE_REDIRECTED = ".autopatch-j 不能重定向到其他位置。"
STATE_CORRUPTED = "任务状态损坏，相关操作已停止；请保留 repair.json 并核查。"
STATE_CONFLICT = "任务或提案已变化，本次操作未执行；请重新查看。"
CONVERSATION_CONFLICT = "会话已被其他操作修改，本轮历史未保存。"
CONVERSATION_SWITCHED = "项目会话已切换，未保存旧运行的历史。"
MEMORY_CONFLICT = "笔记已被其他操作修改，本次修改未完成。"
INVALID_MEMORY_PATH = "笔记路径无效：请使用笔记目录内的相对路径。"
PROTECTED_MEMORY = (
    "MEMORY.md 不能单独删除；可要求 Agent 修订，或用 "
    "/memory clear --confirm 清空笔记。"
)
MEMORY_USAGE = (
    "用法：/memory status｜list｜show [path]｜forget <path>｜"
    "clear --confirm｜export"
)
LOCK_TIMEOUT = "等待项目锁超时，请确认其他操作是否仍在运行。"
INDEX_NOT_READY = "索引尚未构建，请执行 /init。"
INDEX_SCHEMA_INVALID = "索引结构不可用，请执行 /init 重建。"
INVALID_COMMAND = "命令格式错误：请检查引号是否成对。"
NO_PROPOSAL = "当前没有可应用的待审提案。"
NO_REVIEW_PROPOSAL = "当前没有待审提案。"
NO_SCOPE = "当前没有待批准的写入范围。"
INTERRUPTED_DISCARD = "当前任务已中断，请核查写入事实后使用 abort。"
INTERRUPTED_APPLY = "当前提案应用已中断，不能重复 apply；请核查源码后 abort。"
TASK_ACTIVE = "当前任务尚未结束，请先处理或明确结束当前任务。"
TASK_NOT_EDITABLE = "当前任务状态不允许修订。"
NO_ACTIVE_TASK = "当前没有可修订的活动任务。"
EMPTY_SCOPE = "写入范围须包含至少一个明确文件。"
INCOMPLETE_PROPOSAL = "提案须包含文件修改和具体理由。"
TARGET_CHANGED = "修订不能更换当前问题，请先结束当前提案。"
STALE_SCAN = "扫描证据对应的源码已变化，请重新扫描。"
INVALID_ASSESSMENT = "问题判断必须绑定当前调查的问题。"
EMPTY_ASSESSMENT = "问题判断须提供具体依据。"
UNKNOWN_REQUIREMENT = "只能撤回当前仍有效的任务要求。"
OVERLAPPING_REQUIREMENTS = "同一要求不能同时新增和撤回。"
INVALID_LINES = "行号须从 1 开始，结束行不能早于开始行。"
JAVA_BLOCK_ONLY = "语法块读取仅支持 Java，请选择行或全文读取。"
BLOCK_NOT_FOUND = "目标行未命中 Java 语法块，可选择行读取。"
INVALID_REFERENCE_QUERY = "引用查询须提供简单名称和明确的文件或目录范围。"
INVALID_OFFSET = "offset 必须是非负整数。"
SOURCE_CHANGED = "查询期间源码已变化，请重新查询。"
NO_JAVA_FILES = "范围内没有 Java 文件，未执行扫描。"
INVALID_FINDING_HANDLE = "问题句柄须使用扫描返回的 <scan_id>:F<n>。"
SCAN_SOURCE_CHANGED = "扫描期间源码已变化，本次结果未采用；请重新扫描。"
INVALID_SCAN_EVIDENCE = "扫描失败：返回的问题证据不完整或超出扫描范围。"
INVALID_SCAN_OUTPUT = "扫描失败：Semgrep 返回的数据格式无效。"
SCANNER_UNAVAILABLE = "扫描器不可用，请执行 /init。"
SCANNER_LOCK_TIMEOUT = "扫描器初始化失败：等待安装锁超时。"
APPLIED_NO_SCANNER = "源码已应用，但扫描器不可用，未执行复扫。"
APPLIED_SOURCE_CHANGED = "源码已应用，但验证期间源码再次变化，验证结论未采用。"
APPLIED_STATE_NOT_SAVED = (
    "源码已全部写入，但应用状态保存失败；未执行验证，请核查。"
)
APPLIED_CHECKS_CONFLICT = "源码已应用，但任务已变化，验证结果未写入当前任务。"
FAILURE_NOT_SAVED = "失败原因未能保存到任务状态。"
NOT_ROLLED_BACK = "已写入内容未回滚，请核查，勿重复 apply。"
MODEL_AUTH_FAILED = "模型认证失败（401），请检查 API Key。"
MODEL_ACCESS_DENIED = "模型访问被拒绝（403），请检查访问权限。"
MODEL_NOT_FOUND = "接口或模型未找到（404），请检查地址和模型名。"
MODEL_RATE_LIMITED = "模型请求受限（429），请检查额度或稍后重试。"
MODEL_TIMEOUT = "模型请求超时。"
MODEL_INVALID_RESPONSE = "模型返回内容无法解析。"
MODEL_TRUNCATED = "模型输出被截断。"
MODEL_UNFINISHED = "模型未正常完成回答。"
DATABASE_BUSY = "数据库被占用"
DATABASE_INVALID = "数据库文件损坏或格式无效"
DATABASE_FAILED = "项目存储访问失败"
NO_SPACE = "磁盘空间不足"
READ_ONLY = "文件系统为只读"
CERTIFICATE_FAILED = "连接证书校验失败"
DNS_FAILED = "域名解析失败"
PYTHON_MISSING = "无法启动：未找到 Python，请安装 Python 3.14 或更高版本。"
PYTHON_TOO_OLD = "无法启动：需要 Python 3.14 或更高版本。"
VENV_TOO_OLD = "无法启动：现有 .venv 的 Python 版本不符合要求，请重建环境。"
DEPENDENCIES_FAILED = "无法启动：依赖安装失败，请检查上方安装错误。"
OUTPUT_UNAVAILABLE = "CLI 无法输出内容，程序将退出。"
PERMISSION_DENIED = "没有访问权限"
FILE_MISSING = "文件不存在"
MODEL_CONNECTION_FAILED = "无法连接模型服务"
SYSTEM_OPERATION_FAILED = "无法完成文件或系统操作"
OPERATION_TIMEOUT = "操作超时"
INTERNAL_FAILURE = "程序内部错误"
STATE_UNAVAILABLE = "项目状态读取失败，相关状态不可用。"


def _short_reason(reason: str) -> str:
    return " ".join(reason.split())[:160].rstrip("。")


def failed(operation: str, reason: str) -> str:
    return f"{operation}失败：{_short_reason(reason)}。"


def stopped(operation: str, reason: str) -> str:
    if operation == "Agent":
        operation += " "
    return f"{operation}已停止：{_short_reason(reason)}。"


def unavailable(feature: str, reason: str) -> str:
    if feature == "Agent":
        feature += " "
    return f"{feature}不可用：{_short_reason(reason)}。"


def startup_failed(reason: str) -> str:
    return f"项目服务未启动：{_short_reason(reason)}。"


def internal_error(operation: str) -> str:
    return (
        f"{operation}失败：程序内部错误。"
        "可设置 AUTOPATCH_DEBUG=true 后重现查看详情。"
    )


def completion_failed(reason: str) -> str:
    return f"路径补全失败：{_short_reason(reason)}。处理后按 Tab 重试。"


def positive_integer(name: str, *, allow_none: bool = False) -> str:
    suffix = "或 none" if allow_none else ""
    return f"{name} 必须是正整数{suffix}。"


def unknown_command(command: str) -> str:
    return f"未知命令：{command}。可输入 /help 查看用法。"


def unexpected_arguments(command: str) -> str:
    return f"{command}不接受参数。"


def missing_note(path: str) -> str:
    return f"笔记不存在：{path}。"


def clear_interrupted(reason: str) -> str:
    return f"笔记清空中断：{_short_reason(reason)}。已删除的笔记未恢复。"


def incomplete_export(path: str) -> str:
    return f"导出文件可能不完整：{path}。"


def history_not_saved(*, committed: bool) -> str:
    if committed:
        return "任务变更已保存；本轮会话未确认保存。"
    return "本轮会话未确认保存，未覆盖已有任务及并发修改。"


def answer_not_saved(reason: str) -> str:
    return f"会话保存失败：{_short_reason(reason)}。已生成的回答保留在界面。"


def file_failed(operation: str, path: str, reason: str) -> str:
    return f"{operation}失败：{path}，{_short_reason(reason)}。"


def source_decode_failed(path: str) -> str:
    return f"源码读取失败：{path}无法按 UTF-8 或 GBK 无损解码。"


def permission_denied(path: str) -> str:
    return f"没有访问权限：{path}"


def missing_file(path: str) -> str:
    return f"文件不存在：{path}"


def source_unavailable(path: str, reason: str) -> str:
    return f"无法核查当前源码：{path}，{_short_reason(reason)}。"


def line_out_of_range(total: int) -> str:
    return f"行号超出文件范围，当前共{total}行。"


def unsafe_path(path: str) -> str:
    return f"路径必须位于项目源码范围内：{path}。"


def scope_file_required(path: str) -> str:
    return f"写入范围必须指定已有文件：{path}。"


def duplicate_file(path: str) -> str:
    return f"文件重复或指向同一文件：{path}。"


def unauthorized_file(path: str) -> str:
    return f"文件未获写入授权：{path}。请申请范围并等待用户 allow。"


def empty_requirement(name: str) -> str:
    return f"{name}不能为空。"


def unquoted_requirement(requirement: str) -> str:
    return f"新增要求须逐字引用本轮用户原文：{requirement}。"


def replacement_matches(path: str, index: int, count: str | int) -> str:
    return f"{path}第{index}项原文本匹配{count}次，须恰好一次。"


def no_change(path: str) -> str:
    return f"{path}的替换未产生变化。"


def unencodable_replacement(path: str, encoding: str) -> str:
    return f"{path}的{encoding}无法表示替换内容，请调整修改。"


def invalid_candidate(path: str, reason: str) -> str:
    return f"提案未通过语法检查：{path}，{_short_reason(reason)}。"


def missing_scan(scan_id: str) -> str:
    return f"扫描编号无效：{scan_id}。"


def missing_finding(handle: str) -> str:
    return f"该扫描中不存在此问题：{handle}。"


def corrupted_scan(scan_id: str) -> str:
    return f"扫描证据损坏：{scan_id}，请重新扫描。"


def scanner_version(required: str, actual: str) -> str:
    return f"扫描器版本不符：需要 {required}，当前为 {actual}；请执行 /init。"


def scanner_timeout(seconds: int) -> str:
    return f"扫描超时（{seconds} 秒），请缩小范围后重试。"


def saved_state(status: str, progress: str) -> str:
    return f"已知任务状态：{status}。{progress}"


def preflight_failed(path: str) -> str:
    return f"未应用补丁：{path}已变化，请重新准备并审核提案。"


def proposal_invalid(path: str) -> str:
    return f"未应用补丁：{path}的提案内容校验失败，请重新准备提案。"


def apply_interrupted(path: str, reason: str) -> str:
    return f"应用中断：{path}写入失败，{_short_reason(reason)}。"


def source_changed_during_apply(path: str) -> str:
    return f"应用中断：{path}在写入期间被其他操作修改。"


def progress_not_saved(path: str) -> str:
    return f"{path}已写入，但进度保存失败；请核查，勿重复 apply。"


def write_progress(
    written: list[str], remaining: list[str], uncertain: list[str]
) -> str:
    parts = []
    for label, paths in (
        ("已写入", written),
        ("未写入", remaining),
        ("待核查", uncertain),
    ):
        if paths:
            parts.append(label + "：" + "、".join(paths))
    return "；".join(parts) + "。" if parts else ""


def incomplete_new(reason: str) -> str:
    return f"任务已结束，但会话切换未完成：{_short_reason(reason)}。"


def incomplete_reset(reason: str) -> str:
    return f"任务已结束，但索引清理失败：{_short_reason(reason)}。"


def close_failed(reason: str, *, exiting: bool) -> str:
    if exiting:
        return f"资源关闭失败：{_short_reason(reason)}。程序将退出。"
    return f"项目服务关闭失败：{_short_reason(reason)}。本次初始化未完成。"


def model_status(status: int) -> str:
    if status >= 500:
        return f"模型服务异常（{status}）。"
    return f"模型请求被拒绝（{status}）。"


def dependency_missing(component: str) -> str:
    return f"无法启动：必要组件加载失败，{component}；请重新安装项目依赖。"


def input_unavailable(reason: str) -> str:
    return f"CLI 无法接收输入：{_short_reason(reason)}。程序将退出。"
