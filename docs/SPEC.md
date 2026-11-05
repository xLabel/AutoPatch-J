# AutoPatch-J 项目规格

本文维护产品行为、接口和失败边界。需求决定目标，代码与测试分别提供实现和验证证据，不能反向定义需求。协作与授权见 [AGENTS](../AGENTS.md)，工程判断见 [ENGINEERING](ENGINEERING.md)，操作见 [使用手册](GETTING_STARTED.md)。

本项目托管于 GitHub Public 仓库。README 是面向外部读者的项目首页；产品规格、实现边界和工程约束在对应专题维护，任务过程留在会话。项目尚未上线，不为旧入口、配置或 schema 建兼容、迁移与双读；已有用户数据不自动删除。

## 1. 产品能力与架构

AutoPatch-J 是面向企业 Java 仓库的 Python CLI 代码审阅与修复助手，结合项目规范、长期项目知识、静态分析和 AI 判断，交付可审核、可验证的补丁。当前以员工本地交互使用为主，未来支持流水线接入；保留项目问答，不定位为通用聊天工具。

主流程：明确目标 → 收集证据并判断是否应该修改 → 起草一项完整修复 → 人工审核与修订 → 应用 → 验证并反馈下一步。扫描 finding 是候选，零 finding 不能否定用户指出的缺陷；不修改、证据不足和执行失败分别表达。

| 位置 | 职责 |
|---|---|
| `cli/` | 输入、命令、完整预览和人工决定，不编排 finding 批次或重试 |
| `agent/` | 一个项目 Agent、指引、SDK 适配、运行暂存与提交资格 |
| `tools/` | 受控业务函数，SDK 生成 schema、校验参数、反馈纠错 |
| `core/repair/` | 任务和提案类型、准备、审核应用、状态持久化、扫描证据和验证 |
| `core/project/` | 安全路径、源码解码与范围读取、Java 符号索引 |
| `core/conversations.py`、`core/memory/` | 官方会话与长期笔记的项目接入 |
| `llm/` | 模型装配、请求设置、结束校验和客户端生命周期 |
| `scanners/` | managed Semgrep、目标选择和扫描结果归一化 |

SDK 负责 Agent 循环、协议、参数校验、流、reasoning、历史摘要和 Memory；项目自研部分聚焦证据与可审核补丁业务。不实现 Windows Workspace、通用 shell、创建/删除/重命名源码工具、通用构建执行平台或规则治理系统。

## 2. 项目边界、命令与交互

CLI 启动目录就是目标项目根，不向上猜 Git 根。项目状态在 `.autopatch-j/`：`repair.json` 为任务与完整提案，`repair.lock` 为进程间互斥，`findings/` 保存扫描证据，`index.db` 为可重建索引，`conversations.sqlite3` 与 `memory.sqlite3` 分别保存原生消息和长期笔记，`history.txt` 保存终端输入历史。

| 输入 | 行为 |
|---|---|
| 自然语言、`@路径` | 同一 Agent 理解目标和上下文；引用路径不清空任务，也不扩大写权限 |
| `allow` | 批准界面已展示的确切写文件申请，并继续准备当前提案；不修改源码 |
| `apply` | 批准并应用已展示版本的完整提案 |
| `discard` | 结束当前待审任务，丢弃提案；不撤销已写入源码 |
| `abort` | 明确结束当前任务；中断状态先核对界面中的写入证据 |
| `/init` | 准备扫描器并重建索引，保留当前任务与提案 |
| `/status`、`/scanner`、`/reindex` | 状态诊断、扫描器状态、重建索引 |
| `/new` | 结束当前任务，归档项目会话并开启空会话；保留长期笔记 |
| `/reset` | 结束当前任务、删除可重建索引并关闭运行时；保留锁文件、会话、笔记、扫描证据、导出和其他用户文件 |
| `/memory ...`、`/help`、`/quit` | 笔记管理、帮助、资源关闭与退出 |

不再使用五类 intent、审核路由分类器、批次草案队列或重定位。默认问答与讲解只读。模型的语义判断由指引约束；源码修改仍由确定性的人工 `apply` 入口控制，不能宣称模型自动证明了用户意图。

一次至多一项待审修复，可以包含同一问题必需的多文件改动。任务 A 待审时允许只读调查 B，但 B 不能借 A 的身份、提案或权限起草；即使同文件也须先完成 A 或由用户明确 discard/abort/new。失败或部分应用不等于结束任务，须核查并明确结束。新任务不继承旧写权限或临时要求。

## 3. 源码、工具与任务权限

### 3.1 路径与读取

路径只接受仓库相对路径，解析真实目标后须位于仓库内，且不属于 `.autopatch-j/**`，包括指向状态目录或仓库外的软链接。安全的内部源码链接可用。缺失路径直接报告，不从同名索引候选中猜目标；符号索引只用于定位，需读取当前源码。

读取可按需要扩展到仓库内关联代码；写权限单独绑定经用户 `allow` 批准的确切已有文件。目录不能直接作为写权限；扫描可展开明确目录。`@` 只提供上下文线索，不切换任务或授权写入。

写范围申请只登记新增未授权文件，保留已有待批申请；重复申请已授权文件不重开审批，工具明确提示继续准备提案。空文件申请拒绝。

源码返回实际行范围、总行数、截断、编码、无损解码状态及语法块降级信息。行窗口前 20 后 80 行；全文和块读取按最多 300 行、约 30000 字符预算逐步展示，单个物理行保持完整。截断后可按行号继续读取。失败抛出明确错误，不把错误提示拼入源码；没有跨请求共享的 last_* 读取状态或源码缓存。

### 3.2 业务工具

| 工具 | 输入和职责 |
|---|---|
| `start_repair` | `goal, write_paths`；用户明确要求修复时准备新任务与写范围申请 |
| `request_write_scope` | `paths`；申请增加确切文件，等待用户 `allow` |
| `record_constraint` | `requirement`；保存用户明确提出的当前任务临时要求 |
| `scan_java` | `paths`；显式扫描 Java 文件或目录，返回候选与总数，每页 20 项 |
| `get_scan_findings` | `scan_id, offset=0`；继续读取已有扫描候选 |
| `get_finding_detail` | `finding_handle`；原始扫描身份、当前源码、适用规范及证据是否仍对应当前 bytes |
| `search_symbols` | `query`；仓库内 Java 符号定位 |
| `read_source_context` | `path, line=1`；行窗口与适用祖先规范 |
| `read_source_block` | `path, line`；Java 方法、构造器或类型；不可用时退化为目标行起 30 行 |
| `read_source_file` | `path`；有界全文，截断后继续按行读取 |
| `get_proposal` | 当前完整 diff、理由及语法结果，供解释和修订取证 |
| `propose_patch` | `edits, rationale, finding_handle?`；新建或修订同一问题的完整提案 |
| `assess_issue` | `outcome, reason, finding_handle?`；明确登记不修改、证据不足或执行失败 |
| 官方 Memory 四工具 | SDK 原生 notebook 参数，见第 7 节 |

`FunctionToolName` 定义对外名称；schema 来自注解和 docstring。行号拒绝 bool、字符串及小数，未知参数拒绝。工具串行执行，模型没有源码写入、批准范围、apply、discard 或任务清空工具。业务输入可纠正错误经 SDK `ModelRetry` 同轮反馈，每工具最多两次纠错，不由 CLI 重跑整个 Agent。

### 3.3 项目规范与长期知识

根目录 AGENTS 作为正式规范来源注入，读取源码时带回目标祖先 AGENTS；长文截断必须继续读取。用户明确提供或规范明确引用的其他规则文件按需读取，不扫描所有 Markdown。

当前要求、正式规范、长期笔记、源码和扫描证据分别标识。影响当前修复的实质冲突应说明并澄清，不自动创建冲突优先级引擎。可独立检查的要求用现有适合的语法或扫描验证，不为每条规则编写新检查器。临时要求摘取当前用户原文，随任务完成、丢弃、中止或 new 清理，不自动升级为长期笔记。

## 4. 扫描与问题判断

managed Semgrep 使用内置 Java 规则，运行版本和超时见 `AppConfig`；安装用 filelock 互斥并在锁内复查，不静默改用其他扫描器。未准备好时明确报告，用户可执行 `/init`。

成功 payload 必须包含 list 类型 `results`、`errors`，且 errors 为空；进程、JSON、结构或任一 finding 证据出错则整次失败，不登记部分成功或冒充零 finding。证据登记还比较扫描前后文件指纹，变化则拒绝。无 Java 文件表示未扫描。

finding 包含规则、repo-relative path、fingerprint 及精确半开区域：行列从 1 开始、byte offset 从 0 开始。对外句柄为 `<scan_id>:F<n>`，不依赖隐式“当前扫描”，新扫描不重绑旧句柄。扫描快照与每文件原始指纹一起保存，起草关联补丁前再核对当前源码。

Optional 规则约束 `java.util.Optional` 接收者；空值调用 get 抛出的是 `NoSuchElementException`，仍须判断其他前置条件和保护。Random 只在安全敏感用途下考虑 SecureRandom；可复现模拟不据此直接判错。构造参数是否拒绝 null 依业务契约判断。静态候选不等于必须修改。

Agent 逐项判断而非预生成批次补丁。`no_change` 是有效完成结论并记录理由；`insufficient_evidence` 保留待澄清任务，`execution_failure` 表示执行未完成。没有补丁本身不表示失败或整个仓库无问题。

## 5. 补丁安全与人工审核

### 5.1 完整提案与绑定

`edits` 为文件列表，每文件只出现一次，包含有序 `replacements`：`old_text` 非空，在前项替换结果中必须恰好匹配一次；`new_text` 可为空表示删除该片段。重复真实文件、未授权路径、空修改、匹配失效或不唯一，整项准备失败。只操作已有文本文件，不提供文件创建、删除或重命名；无法完成完整修复须说明能力限制。

所有文件先在内存准备，全部成功才生成完整提案。提案包含任务 ID、问题 ID、独立版本 ID、理由、原始与候选 bytes、两者指纹、真实目标、完整 diff、原编码与权限、编辑足迹及校验结果；内存与持久化使用同一模型，不重建另一份状态。完整 bytes 只作本地审核与应用证据，不全量注入提示词。

新建与修订使用同一个工具；修订继承原问题和 finding，不能切换绑定。导入、辅助方法、调用方可属于同一修复，不要求每次编辑与警告区域相交。Java 候选已确认语法错误时反馈纠错，不产生可应用提案；分析不可用单独标为 `unverified`，不伪装通过。

准备或运行失败不替换已有提案。正常或受限结束的有效候选经业务入口提交；失败、异常结束和取消不提交本轮暂存结果。受限运行仍提示“未完整完成”。每次有效修订均重新预览并等待人工 apply。

### 5.2 审核、写入与中断

所有业务状态写入共用进程间锁：提交新任务、范围、临时要求、提案、人工动作、new/reset 均在锁内重读并比较预期任务、状态、版本及提案。LLM 与人工等待在锁外，过期运行拒绝覆盖新版本；完整源码应用和写入进度保存期间持锁；复扫在锁外执行，保存验证结果时再核对版本，不覆盖后续任务。锁文件显式保留，不随 reset 删除。

apply 绑定界面展示的版本；先重读所有目标，比较完整原始 bytes、真实路径及提案指纹，扫描目标也核对原指纹。任一预检失败则零写入，须重新准备与审核，不重定位旧提案。

完整预检通过后先持久化 `applying` 与预期结果，再逐文件写入、保存真实进度。每文件在同目录临时文件完成编码、flush、fsync，保留 permission mode，最后再次比较原 bytes 并 `os.replace`。匹配时规范化换行，写入只替换对应 bytes，其他 bytes 不重编码；保留 UTF-8/GBK、BOM、换行与无尾换行。

多文件不承诺全部成功或自动撤销。第一个 IO、路径或持久化故障后停止，报告已写入、未写入和不确定项，不自动回滚或重新 apply。写后保存失败明确保留写入事实；磁盘 applying 状态阻止重放。重启只展示持久进度与当前 bytes 对照，文件等于候选不能单独证明执行历史，须人工核查再 abort/new。最终比较之后的非协作竞争写入仍可能存在，不宣称消除所有外部竞争。

损坏或不合法的修复状态明确拒绝，不当作 idle，不自动重建或迁移。交互输入前读取或展示状态失败时，报告一次并关闭资源，以非零状态退出，保留磁盘数据。discard、abort、new 和 reset 不撤销已经应用的源码。

### 5.3 应用后验证

成功写入、语法通过、目标规则未再触发、编译及业务验收是不同事实。所有文件成功写入且 applied 状态保存后才验证，失败或中断不继续复扫。记录语法结果、目标复扫及未执行的编译/测试。

有可靠扫描绑定时，将原目标半开区间依次映射经过每次真实替换；辅助文件修改不强制覆盖目标区域。验证前后确认预期 bytes 未变，复扫同路径同规则的映射足迹。足迹内仍命中为 `failed`，未命中为 `passed`；其他位置同规则不混作原目标。缺目标、扫描器、可靠源码或复扫失败均为 `unverified`。

上述 passed 仅证明目标足迹没有再触发同规则，不证明业务语义正确。没有扫描绑定的用户缺陷不能伪造目标复扫通过。编译、测试、业务验收当前未自动执行，明确 `unverified`；验证失败不回滚已批准写入，applied 事实仍保留。

## 6. LLM、上下文与诊断

### 6.1 配置与协议

Python 3.14+；固定 `pydantic-ai-slim[openai]==2.63.0`、`pydantic-ai-harness==0.63.0`、`openai==3.40.0`、`filelock==4.2.4`。完整解析版本在 `constraints.txt`，安装使用 `python -m pip install -c constraints.txt -e '.[test,dev]'`。固定依赖不代表固定模型服务端版本。

| 环境变量 | 默认值与用途 |
|---|---|
| `AUTOPATCH_LLM_API_KEY` | 必填；没有 Key 仍可运行本地命令 |
| `AUTOPATCH_LLM_BASE_URL` | `https://api.deepseek.com` |
| `AUTOPATCH_LLM_MODEL` | `deepseek-flash` |
| `AUTOPATCH_LLM_CONTEXT_WINDOW` | 未知部署必须明确的上下文容量 |
| `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS` | 未知部署必须明确的输出预算 |
| `AUTOPATCH_DEBUG` | `false`；前台展示更多推理与有界错误详情 |

只读启动环境，不读 `.env`。内置 Flash 与 deepseek-v4-pro 固定容量，不接受覆盖；其他模型必须明确容量和输出预算。旧模型名不转换，不保留已删除的批次参数。

Flash 上下文为 1,048,576 tokens，服务端最大输出 393,216；应用输出上限 128,000，安全余量 32,768，输入预算 887,808，见 [模型官方数据](https://api-docs.deepseek.com/zh-cn/api/list-models/)。DeepSeek 请求开启 thinking、reasoning_effort=max，公开 profile 选择 max_tokens；其他模型沿用 SDK baseline，不宣称已验证所有部署。摘要同模型、非流式、120 秒超时。

SDK 管理 Chat Completions、参数 schema、流、reasoning 回传；没有第二套 parser。每个 CLI runtime 拥有单个 asyncio.Runner 和异步客户端，跨请求复用，退出或装配失败时关闭；不启用默认外部 instrumentation 或 SDK banner。

### 6.2 请求额度、上下文与正常结束

使用原生 `UsageLimits()` 默认每轮 50 次逻辑模型请求，摘要与工具纠错共享；这不是上下文压缩，也不是费用上限。网络最多两次原生重试，不叠加通用 retry，不重放已消费的流或业务工具流程。

只接受原始 finish_reason=stop/tool_calls，开启严格流结束标记。公开 after_model_request hook 在工具执行前拒绝截断、过滤、资源不足、aborted、缺失和未知状态；同一校验用于摘要。SDK 对缺字段的原生映射不等于证明原始字段存在。

上下文只用原生 SummarizingCompaction，输入预算的 85% 触发、保留默认最近 20 条消息及工具依赖。估算采用 SDK 的 provider usage 与原生启发式，不用 UTF-8 字节计数。当前用户原文、目标、写范围、临时约束和提案身份通过公开请求指令独立提供，不依赖摘要恢复。

摘要是尽力压缩：无可摘要前缀或摘要不更小时保留原历史，本轮不重复无收益摘要，不裁掉必需内容，不回退清工具结果或滑动窗口。摘要失败或供应商拒绝超限即停止当前运行，保留原任务和提案，不提交本轮暂存结果；不声称本地估算能证明完整 DeepSeek 请求一定容纳。

DetectLoops 使用 SDK 默认 nudge 行为，不保留三次即报错的自研硬停。运行区分 completed、limited、failed、cancelled。失败或取消不能保存残缺会话、不能替换原提案；此前已成功保存的长期笔记不随之后模型失败回滚。

### 6.3 诊断边界

普通模式隐藏原始供应商错误，debug 前台展示最多 20000 字符的异常/body，按普通文字显示 Rich 标记。不主动附加请求或 headers，不保证供应商回显已脱敏。

笔记和会话预期存储故障明确提示，不当作空数据；contract 与编程错误继续传播。回答生成后会话保存失败保留回答，说明未保存，并发 CAS 不覆盖其他进程。业务提交先于成功会话保存，过期业务结果不保存成成功修复回合。

## 7. 项目 Memory

### 7.1 统一会话

官方 SqliteConversationStore 保存完整 SDK 消息，包括 reasoning、工具调用、工具结果和实际摘要后的 head。启动恢复本项目最近未归档会话，问答、读取、扫描、修复及提案解释共用原生历史；不拼接另一套消息、不启用自动重放或 StepPersistence。

仅完整成功回合保存；失败、取消和受限回合不保存残缺消息。任务、提案和写权限独立持久化，模型历史不是审核状态。业务提交失败不保存该回合为成功会话；回答已生成而会话保存失败仍可显示回答和实际业务状态。

### 7.2 长期笔记

官方 Memory + SqliteMemoryStore 保存 notebook，namespace=project、agent_name=main。原生四工具为 read_memory、write_memory、delete_memory、search_memory，SDK 负责注入、文件操作和版本冲突，见 [官方 Memory 文档](https://pydantic.dev/docs/ai/harness/memory/)。

只记录用户明确表达或确认的长期项目偏好与决定；不从源码、扫描、一次修订或 AI 自己的结论自动提炼永久规则。此约束由指引落实，不宣称 SDK 自动理解业务。临时任务要求与正式项目规范各有来源和生命周期，必要冲突在当前对话澄清。

### 7.3 管理与数据边界

| 命令 | 行为 |
|---|---|
| `/memory status`、`list` | 读取实际文件列表，故障不显示成空笔记 |
| `/memory show [path]` | 显示笔记，默认 MEMORY.md |
| `/memory forget <path>` | 删除指定笔记；主 MEMORY.md 受保护，需明确修订或 clear |
| `/memory clear --confirm` | 清空笔记，保留项目会话、导出和终端历史 |
| `/memory export` | 独占创建现有笔记 JSON 导出，不覆盖旧文件 |

各项目数据库隔离，无跨项目或云端 Memory。删除笔记不擦除旧会话，指引禁止据旧会话恢复已删除笔记。旧 memory.db、memory_summary.md 和已有导出不读取、不迁移、不自动删除。损坏数据库不自动重建，也不承诺任意损坏均可导出或清空。会话和笔记可能包含用户原文、源码及 reasoning；导出只含 notebook。

## 8. 验证契约与能力限制

`pytest -q` 是确定性完整回归，默认阻止真实 OpenAI client 和原生模型网络调用；只允许明确 FunctionModel/TestModel 或 MockTransport。扫描器与传输可以替代，补丁判断、文件 IO、状态转换、人工绑定及 SQLite 持久化保留真实组件。

真实 LLM 用例标记 live_llm，需本次明确同意并以 `--run-live-llm` 启用；先行确定性失败或首个 live 失败阻止后续请求。输出只显示模型和安全 endpoint origin。授权要求见 [AGENTS](../AGENTS.md#实现与验证)。

自动化未覆盖 Java 编译、测试、业务验收和流水线交付；静态无 finding 不证明无缺陷，摘要估算不保证服务端容量，多文件应用不具备整体回滚。平台验证以实际执行为准。

## 9. 代码与测试证据索引

| 领域 | 代码 | 测试 |
|---|---|---|
| 任务、提案与状态 | [领域模型](../src/autopatch_j/core/repair/models.py)、[业务入口](../src/autopatch_j/core/repair/service.py)、[存储](../src/autopatch_j/core/repair/store.py) | [完整提案与失败恢复](../tests/test_repair.py) |
| 字节准备与原子写入 | [文件操作](../src/autopatch_j/core/repair/files.py) | [字节、预检与 IO 故障](../tests/test_repair.py) |
| 扫描证据与验证 | [证据](../src/autopatch_j/core/repair/evidence.py)、[复扫](../src/autopatch_j/core/repair/verification.py)、[语法](../src/autopatch_j/core/repair/checks.py) | [目标足迹及误报判断](../tests/test_patch_verifier.py)、[SDK 完整闭环](../tests/test_repair_integration.py)、[语法](../tests/test_syntax_checker.py) |
| 统一 Agent 与 SDK | [Agent](../src/autopatch_j/agent/agent.py)、[请求边界](../src/autopatch_j/agent/boundaries.py)、[工具](../src/autopatch_j/tools/project.py) | [离线 SDK 与 SQLite](../tests/test_sdk_agent.py) |
| 读取与规范来源 | [源码](../src/autopatch_j/core/project/source_reader.py) | [读取和祖先规范](../tests/test_source_read_tools.py) |
| 人工交互 | [输入](../src/autopatch_j/cli/input_router.py)、[主循环](../src/autopatch_j/cli/app.py) | [实际 CLI](../tests/test_cli_commands.py) |
| 真实集成 | [授权门禁](../tests/conftest.py) | [真实模型用例](../tests/test_live_sdk.py)，默认不运行 |
