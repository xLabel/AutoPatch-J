# AutoPatch-J 项目规格

本文统一维护项目行为、接口和关键边界。用户明确确认的需求优先；代码用于核实当前实现，测试用于核实覆盖和结果，二者不能自行改变需求。已确认需求与实现冲突时，标明差异并修正实现，不能反向改写要求。

项目尚未上线，不为历史版本、旧 schema、配置或工作流增加兼容、迁移及双读。当前版本的故障处理、中断恢复和数据保护仍属于有效行为。协作与验证操作见 [AGENTS](../AGENTS.md)，设计取舍与工程实践见[工程指南](ENGINEERING.md)，安装和日常操作见[使用手册](getting_started.md)。

## 1. 产品能力与架构

AutoPatch-J 是面向 Java 仓库的 Python 3.14+ 交互式 CLI Agent。当前闭环是：解析请求范围 → 静态扫描 → 按 finding 取证与起草补丁 → 人工审核 → 应用 → 重扫目标。另支持代码解释、普通问答和项目级 Memory。

当前实际扫描引擎为 managed Semgrep；运行时仅登记可执行实现，后续扫描器方向见 README 的展望。Java 草案语法检查使用 Tree-sitter，不等于编译或业务测试。自动执行目标项目的构建、测试、CI/CD 集成和创建 MR/PR 尚未实现。

| 层 | 当前职责与主要入口 |
|---|---|
| `cli/` | 命令、输入路由、workflow、终端展示；`app.py` 装配入口 |
| `agent/` | 任务画像与 prompt、SDK Agent 适配、请求权限和草案提交资格 |
| `tools/` | 受控业务函数和补丁草案构造；SDK 从注解生成 schema |
| `core/` | finding/补丁领域模型、审核应用协调、源码与索引、会话及笔记存储接入 |
| `llm/` | 原生模型装配、固定任务策略、容量与结束校验、资源作用域 |
| `scanners/` | 扫描器目录、managed runtime、目标选择及结果归一化 |

此表是当前代码导航，内部文件和抽象可随已确认需求调整；职责组织的工程要求见[工程指南](ENGINEERING.md#3-职责类型与命名)。

## 2. 项目边界、命令与输入路由

启动 CLI 时所在目录就是项目根，不向上查找 Git 根。项目状态写入该目录的 `.autopatch-j/`：扫描快照位于 `findings/`，审核状态为 `workspace.json`，符号索引为 `index.db`，普通 SDK 会话为 `conversations.sqlite3`，项目笔记为 `memory.sqlite3`，终端历史为 `history.txt`。

| 命令 | 行为 |
|---|---|
| `/init` | 初始化 runtime，清空补丁审核工作台，准备 managed Semgrep 并重建 Java 索引 |
| `/status`、`/scanner` | 分别展示项目诊断、扫描器状态 |
| `/reindex` | 重建符号索引 |
| `/reset` | 清理 review、scan、index 及请求缓存；保留普通会话、笔记、导出与终端历史 |
| `/new` | 归档普通会话并开启空会话；中止待审草案并清理临时状态，保留项目笔记 |
| `/memory ...` | 管理 Memory，详见第 7 节 |
| `/help`、`/quit` | 显示帮助；执行退出收尾并退出 |

只输入 `@路径` 时提示补充具体要求。斜杠命令走命令路由；无 pending patch 时进入新任务；pending 时显式 `@scope` 表示新任务，会清除旧审核工作台，其余模糊输入由 review route classifier 判断是否继续审核。

业务意图为 `code_audit`、`code_explain`、`general_chat`、`patch_explain`、`patch_revise`。没有 pending patch 时不得接受 patch-only intent。分类失败时使用确定性默认值：无 pending 为 `GENERAL_CHAT`，pending intent 为 `PATCH_EXPLAIN`，pending route 为 `REVIEW_CONTINUE`。每个需要模型分类的分类器只做一次短逻辑调用，不做输出重生成或 Agent 二次分类；debug 保留失败原因。

## 3. 源码、工具与任务权限

路径经归一化和真实目标解析后必须位于仓库内；拒绝越界路径和落入 `.autopatch-j/**` 的路径，包含软链接。该边界覆盖 focus、源码 scope、符号索引及搜索、扫描 targets/results 和 source-read。指向正常仓库源码的内部软链接可用，状态目录不因精确路径或旧索引记录而获豁免。

源码请求路径缺失时，只允许使用唯一的 in-focus 同名索引候选；多个候选返回候选列表并要求明确路径。当前锁定的 focus 同时约束读取与补丁目标。

| 工具 | 输入与职责 |
|---|---|
| `get_finding_detail` | `finding_id` 扫描内句柄；返回身份、当前绑定区域和源码证据，句柄不是规则 ID |
| `search_symbols` | `query`；查 Java 类、方法等符号并返回定位信息 |
| `read_source_context` | `path, line`；1-based 目标行，默认前 20、后 80 行，范围受文件边界限制 |
| `read_source_block` | `path, line`；读取所在 Java 方法、构造器或类型块，解析不可用或未命中时退化为目标行起的最多 30 行 |
| `read_source_file` | `path`；请求全文；目录和大型 Java 文件受防线限制，非 Java 文件最多展示 200 行 |
| `propose_patch`、`revise_patch` | `file_path, old_string, new_string, rationale, associated_finding_id?`；暂存新草案或当前补丁的替代草案，不直接修改源码或审核队列 |
| `write_memory`、`read_memory`、`delete_memory`、`search_memory` | 官方 Memory 工具及原生参数，围绕 notebook 文件操作；不作为源码或 finding 证据 |

工具名由 `FunctionToolName` 统一定义，schema 由 SDK 从注解、Field 描述及 Google 格式 docstring 生成；整数拒绝 bool、字符串或小数冒充，未知参数拒绝。每个任务只导出白名单工具，执行器再次拒绝未开放工具。

| 任务 | 可用工具 |
|---|---|
| `code_audit` | finding detail、三种源码读取、propose、Memory 读写、搜索与删除 |
| 零 finding 二次复核 | 三种源码读取、propose、Memory 读写、搜索与删除 |
| `code_explain` | 三种源码读取、Memory 读写、搜索与删除；允许导航时增加 symbol search |
| `general_chat` | Memory 读写、搜索与删除 |
| `patch_explain` | symbol search、三种源码读取、Memory 读写、搜索与删除 |
| `patch_revise` | symbol search、三种源码读取、finding detail、revise、Memory 读写、搜索与删除 |

自然语言 prompt 用 Markdown section 分开任务、用户原文、证据与执行要求；代码和 diff 使用代码块，分类器保留 `<<<USER_TEXT ... USER_TEXT` 原文边界。function schema 和 SDK 消息保持原生结构。提示词中的取证要求与程序侧权限、绑定校验共同生效，不能把提示词要求宣称为程序已证明的事实。

## 4. 扫描与有界审计

Semgrep 使用项目内置 Java 规则和 AutoPatch-J 管理的运行时，当前固定版本和扫描超时由 `AppConfig` 管理；安装使用 filelock 系统互斥锁，等待超时后失败，取得锁后再次检查是否已经安装。缺少运行时须通过 `/init` 准备，不静默选用其他扫描器。

成功 payload 必须显式含 list 类型的 `results` 与 `errors`，且 `errors` 为空。无效 JSON、进程失败、超时、缺字段、非空 errors 或任一 finding 证据不合法时，整次扫描失败；不得保存部分成功 artifact、进入 backlog 或冒充零 finding。没有可扫描目标的 `skipped` 与成功零 finding 分别表达。

每个 finding 保存应用侧 fingerprint、engine、规则 ID、repo-relative POSIX path 和精确 region。region 使用 1-based line/column、byte offset 与半开区间；fingerprint 结合版本、engine、规则、路径和匹配源码，同证据重复项按源码位置确定性区分，不依赖 Semgrep 登录指纹或 raw result 顺序。

每次用户请求最多处理 `audit_batch_limit` 个 finding；超额时保留已生成的待审补丁并提示用户确认后重新检查，不承诺跨请求保存剩余 finding backlog。再次审计重新扫描当前源码。可重试的 finding 工具失败最多增加一次强制重读尝试，不表示任意失败均自动重试。

成功扫描为零 finding 时，逐文件进入 LLM 二次复核，找到首个可入队草案后停止该流程；finding 批次限制不是零 finding 文件数上限。扫描无报错、LLM 未起草补丁，都不证明代码没有缺陷。

每次 Agent 运行最多 10 次逻辑模型请求，摘要与纠错共享该额度。官方 DetectLoops 在重复结果或交替调用达到阈值时停止，不因相同工具参数本身判定循环。普通模式折叠 reasoning 与 observation 细节；debug 展示更多信息，workflow 可强制紧凑 observation。终端渲染与业务状态分开维护。

## 5. 补丁安全与人工审核

### 5.1 草案与绑定

search-replace 的 `old_string` 必须在当前源码中唯一匹配；引擎按规范化换行定位，并保存原始 bytes 上的 `match_region`。关联扫描 finding 时保存句柄、scan ID 和完整目标 identity，补丁区域须与当前 finding region 相交。同文件不等于命中同一 finding。

工具先将草案暂存于请求 session；正常或受限结束后，workflow 校验有效草案并入队。异常或取消不提交本轮暂存草案。Java 草案附 Tree-sitter 语法结果；`invalid` 或 `unavailable` 仍须准确显示，不能声称所有可预览草案均通过语法、编译或测试。用户确认 apply 才写源码。

revision 只能替换当前文件和当前 finding，省略句柄时继承原绑定；提供不同句柄或区域失配时拒绝，原草案不变。当前补丁的局部运行约束按 review item 绑定，不能泄漏给同文件其他补丁。

### 5.2 审核与落盘

`apply` 应用当前补丁，`discard` 丢弃当前项，`abort` 中止审核并丢弃剩余项。解释为只读任务，修订仍须重新预览与确认。

apply 前确认所选草案仍等于当前持久待审项，再重读源码，确认 old string 仍唯一且区域等于草案绑定。无法无损解码、重新编码失败或源码绑定变化时拒绝。写入仅替换匹配区域 bytes，区域外 bytes 保持一致；replacement 沿用文件编码和检测到的换行形式，保留可观察的 permission mode。

在同目录临时文件完成编码、写入、flush、fsync，再比较目标当前 bytes 并原子替换。原子替换前失败或比较已发现的外部修改不会覆盖当时目标；不承诺消除最终比较之后的非协作竞争写入。

源码写入前，先把当前及同文件待审草案持久化为阻断状态；阻断状态保存失败则不写源码。写入成功后再保存 applied、重定位和队列状态。最终保存失败时明确报告“源码已写入、审核状态未保存”，停止复扫，保留失效草案阻止重复应用；重启后同样要求核对源码，再 discard 或 abort 并重新扫描，不自动回滚或恢复执行。

前序补丁应用后，按真实 source/changed region 更新同文件非重叠 pending 草案及 finding 区域，刷新 diff 与语法结果。重叠、匹配丢失、不唯一或偏离预期位置时标为 `STALE_DRAFT`，禁止 apply/revise，提示 discard 或 abort 后重新扫描。finding detail 使用工作台当前绑定，不能退回初始位置。

### 5.3 应用后验证

成功写入与确认问题消失是两个事实。verifier 先检查成功 apply 证据：source 等于草案 match region、source/changed 起点一致、source 仍与目标相交。证据不足时返回 `UNVERIFIED`，不盲目重扫。

验证把原 finding region 按真实编辑映射为 apply 后足迹，使用同路径、同规则 candidate 判断目标是否仍在；仅看 replacement 或文件内是否还有同规则都不足。保留的目标前缀、后缀也须计入，足迹外同规则项只算其他剩余问题。零长度删除点按区域包含关系处理，结束于该点的相邻项不自动算目标。

| 结果 | 含义 |
|---|---|
| `RESOLVED` | 目标映射足迹内不再触发规则，其他位置可仍有同规则 finding |
| `STILL_PRESENT` | 目标映射足迹内仍触发规则 |
| `UNVERIFIED` | 无可靠目标/应用证据、扫描器不可用或重扫失败等，不能确认 |

没有 scanner finding 绑定的 LLM 二次复核草案无法做精确目标验证。验证失败不自动回滚已确认并成功写入的补丁，审核项仍记录 applied，CLI 同时显示验证结论。

## 6. LLM、上下文与诊断

### 6.1 配置与协议

运行基线为 Python 3.14+。项目固定 `pydantic-ai-slim[openai]==2.63.0`、`pydantic-ai-harness==0.63.0`、`openai==3.40.0` 和 `filelock==4.2.4`。runtime/test/dev 的精确解析版本见根 `constraints.txt`，安装使用 `python -m pip install -c constraints.txt -e '.[test,dev]'`。依赖锁定不固定模型服务端版本。

| 环境变量 | 默认值与用途 |
|---|---|
| `AUTOPATCH_LLM_API_KEY` | 必填；无 Key 时仍可使用本地命令 |
| `AUTOPATCH_LLM_BASE_URL` | `https://api.deepseek.com`；标准 Chat Completions 地址 |
| `AUTOPATCH_LLM_MODEL` | `deepseek-flash` |
| `AUTOPATCH_LLM_CONTEXT_WINDOW` | 未知部署必须明确提供的上下文容量 |
| `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS` | 未知部署必须明确提供的输出预算 |
| `AUTOPATCH_DEBUG` | `false`；展示 reasoning、工具观察及前台错误详情 |
| `AUTOPATCH_AUDIT_BATCH_LIMIT` | `4`；一次审计处理的 finding 数量 |

配置只读启动进程环境，不自动读取 `.env`。内置 `deepseek-flash` 与 `deepseek-v4-pro` 使用固定容量，不接受覆盖；其他模型必须同时指定容量与输出预算。旧模型别名不自动转换，任意 extra body、reasoning effort 和 stream dialect 不作为用户配置。

Flash 的官方上下文为 1,048,576 tokens，服务端最大输出为 393,216；应用操作输出上限为 128,000，协议安全余量为 32,768，输入预算为 887,808。[官方模型数据](https://api-docs.deepseek.com/zh-cn/api/list-models/)

审计、解释、草案与修订开启 DeepSeek thinking，使用 `reasoning_effort=max`；分类非流式，关闭 thinking、temperature=0、输出最多 128；摘要使用同一模型，非流式且超时 120 秒。通过公开 OpenAI model profile 选择 DeepSeek 的 `max_tokens`；其他明确配置的模型使用 SDK baseline，不发送 DeepSeek 专属字段，不宣称已验证全部部署。

分类和笔记管理的异步取消同样终止当前交互并收尾，不按分类失败默认值继续调用模型。

SDK 负责 Chat Completions、流解析、reasoning 回传、schema 和参数校验，不维护第二套 wire/parser。SDK 将缺失的 reasoning 映射为空字符串等行为按原生语义处理，不宣称已证明响应原始字段存在。每个 CLI runtime 拥有单个 asyncio.Runner 和异步客户端，跨请求复用并在退出或装配失败时关闭；不启用 SDK 宣传 banner 和默认外部 instrumentation。

### 6.2 请求额度、上下文与正常结束

每次 Agent.run 最多 10 次逻辑模型请求，摘要、工具参数纠错共同计入；分类另计一次，不做输出重生成。SDK 网络重试最多两次，不叠加通用重试或重放已消费的流。

只把原生 `finish_reason=stop/tool_calls` 当作正常结果。开启严格流结束标记，并在公开 after_model_request hook 校验原始结束状态；截断、过滤、资源不足、aborted、缺失和未知状态不能执行该响应工具、提交本轮草案、保存成功普通会话或采用为有效摘要。同一校验覆盖分类和摘要。此前已成功写入的笔记不随之后的模型失败自动回滚。

请求结果区分 completed、limited、failed、cancelled。额度或循环限制可保留经业务校验的草案供人工审核；失败和取消不提交暂存草案。受限、失败和取消提示不随最终答案抑制而隐藏。未完成的零 finding 复核不能显示“未发现问题”，部分过程展示也不表示成功完成。

使用官方 ClearToolResults、SummarizingCompaction、SlidingWindowCompaction 和 DetectLoops。输入预算达到 80% 时清理旧的可重读工具结果，85% 时尝试摘要；摘要保留最近 20 条消息和必要工具依赖，预期模型失败可回退窗口。保留当前用户原文、任务约束和程序绑定的独立投影，不交给摘要替代。DetectLoops 的重复调用次数不设阈值，重复结果和交替调用阈值均为 3，触发即停止。

最终通过公开 WrapperModel 在委托 SDK 前核对完整消息、指令、function/output tool schema、请求设置及笔记；中文计入 UTF-8 字节量。压缩使用同一保守估算量纲。它可能提前压缩或拒绝，不是精确 DeepSeek tokenizer 或容量证明；必需内容仍超限则明确停止，不静默裁剪。

### 6.3 诊断边界

普通模式显示简明错误；debug 的前台错误最多展示 20,000 字符的异常/body，按文字显示 Rich 标记。格式化不主动附加请求、headers 或认证信息，也不保证供应商回显内容已脱敏。

会话和笔记的预期存储故障明确提示，不当作健康空结果；并发冲突不覆盖对方，contract 与编程错误继续传播。回答已生成而普通会话保存失败时，保留回答并说明未保存。异常展示是最外层边界，不改变业务成功事实。

## 7. 项目 Memory

### 7.1 普通会话

官方 SqliteConversationStore 保存完整 SDK 消息，包括 reasoning、工具调用与结果以及实际压缩后的 head，位置为 `.autopatch-j/conversations.sqlite3`。启动恢复本项目最近未归档的普通会话，使用 conversation_id 延续，不拼接重叠快照。只保存完成的普通请求；失败或取消不把残缺回合留给下次请求。

普通聊天与代码讲解加载和保存普通历史。代码审计、零 finding 复核、补丁解释和修订使用独立历史，不读写普通会话。所有任务可访问本项目长期笔记；笔记不能覆盖当前用户指令、focus、源码证据、finding 绑定及人工审核。

### 7.2 长期笔记

官方 Memory＋SqliteMemoryStore 保存 Agent 维护的 notebook，位置为 `.autopatch-j/memory.sqlite3`，固定 namespace 为 `project`、agent_name 为 `main`。SDK 管理原生文件操作、根笔记注入和版本冲突；项目不维护后台提取、候选合并、job、lease 或 Markdown 审阅投影。[官方 Memory 文档](https://pydantic.dev/docs/ai/harness/memory/)

Agent 指引只记录用户明确表达或确认的长期项目偏好与决定，不从一次补丁纠正、源码、工具输出或 AI 单方结论推导永久规则。此语义由指引约束，不宣称 SDK 已自动证明。当前补丁的临时要求按审核项保存，完成、丢弃、中止或 /new 后清理。

### 7.3 管理与数据边界

| 命令 | 行为 |
|---|---|
| `/memory status`、`list` | 读取当前笔记文件列表；实际存储异常直接报告 |
| `/memory show [path]` | 显示文件，默认 MEMORY.md |
| `/memory forget <path>` | 删除指定笔记；主 MEMORY.md 受保护，须明确要求 Agent 修订，或使用 clear |
| `/memory clear --confirm` | 删除当前笔记，保留普通会话、导出及终端历史 |
| `/memory export` | 独占创建现有笔记的 JSON 导出，不覆盖旧文件，不宣称 RAW 审计快照 |

本进程操作在同一同步入口顺序执行；存储使用原生版本条件防止覆盖并发修改。删除笔记不擦除旧会话中已经出现的内容，指引禁止从旧历史自行恢复已删除笔记。各项目数据库独立，不提供跨项目共享或云端记忆。

旧 memory.db、memory_summary.md 和已有导出不读取、不迁移，也不自动删除。/reset 保留这些已有用户数据以及新的会话与笔记。损坏数据库不会自动重建，不承诺任意损坏均可导出或清空。会话与笔记可能包含用户原文、推理或源码，导出仅包括 notebook 内容。

## 8. 验证契约与能力限制

`pytest -q` 为确定性完整回归。普通测试阻止真实 OpenAI/AsyncOpenAI 构造和原生模型网络请求，只允许明确的 FunctionModel/TestModel 或 MockTransport；本机存在有效 Key 不能触发真实调用。扫描编排使用明确的进程或扫描边界替身，源码、绑定、审核及持久化判断保留真实组件。

真实 LLM 测试标记 live_llm，仅 `--run-live-llm` 启用，且排序在确定性测试之后；先行失败或首个 live 失败阻止后续请求。配置摘要只显示模型和安全 endpoint origin。是否运行、平台及结果按本次实际证据报告，授权见 [AGENTS](../AGENTS.md#实现与验证)。

能力限制：Java 编译、测试及业务验收尚未自动接入；静态无 finding 不证明无缺陷，RESOLVED 只表达目标规则足迹的复扫结论。笔记不能证明代码事实，保守估算不能保证服务端容量。未启用 StepPersistence、自动执行恢复、额外会话命名模型或持久化调度引擎。

## 9. 代码与测试证据索引

| 领域 | 代码入口 | 相关测试 |
|---|---|---|
| SDK、流、额度与结束 | [Agent](../src/autopatch_j/agent/agent.py)、[策略](../src/autopatch_j/llm/policy.py)、[装配](../src/autopatch_j/llm/factory.py) | [离线 SDK 场景](../tests/test_sdk_agent.py)、[容量](../tests/test_context_window.py) |
| 普通会话与笔记 | [会话](../src/autopatch_j/core/conversations.py)、[笔记](../src/autopatch_j/core/memory/__init__.py) | [SDK 与存储](../tests/test_sdk_agent.py) |
| 路由与展示 | [输入](../src/autopatch_j/cli/input_router.py)、[展示](../src/autopatch_j/cli/agent_stream_presenter.py) | [CLI](../tests/test_cli_commands.py)、[路由](../tests/test_conversation_router.py)、[流式展示](../tests/test_agent_stream_presenter.py) |
| 范围与扫描 | [项目](../src/autopatch_j/core/project/)、[Semgrep](../src/autopatch_j/scanners/semgrep/) | [源码](../tests/test_source_read_tools.py)、[焦点](../tests/test_focus_guardrails.py)、[扫描身份](../tests/test_semgrep_finding_identity.py) |
| 补丁与审核 | [补丁](../src/autopatch_j/core/patching/)、[审核服务](../src/autopatch_j/core/review/application.py) | [字节与原子写入](../tests/test_core_patch.py)、[草案](../tests/test_patch_draft_tools.py)、[验证](../tests/test_patch_verifier.py)、[工作台](../tests/test_workspace_manager.py) |
| 真实集成 | [显式授权门禁](../tests/conftest.py) | [Flash 与临时 Java 闭环](../tests/test_live_sdk.py)，默认跳过 |
| 分层测试 | [测试入口](../tests/conftest.py) | [授权及顺序](../tests/test_layered_test_execution.py) |
