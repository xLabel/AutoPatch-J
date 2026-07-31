## Purpose

定义 AutoPatch-J 普通对话 Memory 的持久化、线程生命周期、后台处理、渐进检索、上下文隔离及用户审计管理契约。

## Requirements

### Requirement: SQLite 持久化普通对话事实
系统 SHALL 使用项目级 `.autopatch-j/memory.db` 作为 thread、turn、Memory job、candidate、semantic item、revision 与 provenance 的唯一运行时事实源，并 SHALL 以 schema v4 的事务和约束保持一致性。合法 bootstrap state SHALL 同时包含唯一 `memory_meta(id=1)` 和唯一 active thread。

#### Scenario: 首次初始化 Memory v4
- **WHEN** 当前 repo 尚无 `memory.db`
- **THEN** 系统创建 `user_version=4` 的干净 SQLite schema
- **AND** 为 repo 创建唯一 active thread

#### Scenario: 拒绝既有非 v4 database
- **WHEN** 既有 `memory.db` 的 `user_version` 不是 4，或 v4 catalog、meta singleton、唯一 active thread 不完整
- **THEN** Memory 进入 degraded 状态且不修改现有文件
- **AND** 不执行 migration、双读或自动删除，只有显式 `/memory clear --confirm` 才创建干净 v4 database

#### Scenario: 遗留 memory JSON
- **WHEN** 初始化发现 `.autopatch-j/memory.json`
- **THEN** 系统不读取、不迁移且不自动删除该文件
- **AND** 运行时事实源仍只认健康的 v4 database

#### Scenario: 并发写入 turn
- **WHEN** 多个 manager 或 CLI 并发向同一 repo 追加 turn
- **THEN** 每个已提交 turn 均被保留且 sequence 唯一
- **AND** repo 仍只有一个 active thread

#### Scenario: turn scope projection 有界
- **WHEN** 任一 workflow 为新 turn 提供超过 `MAX_SCOPE_PATHS=10` 条 scope path
- **THEN** Store 按输入顺序只持久化前十条并让 extraction payload 使用同一有界投影
- **AND** 实际扫描、Agent focus 与 patch 范围保持完整，不迁移或读取时截断既有 turn

### Requirement: 普通 thread 跨重启延续
系统 SHALL 在主 LLM 调用前保存 RAW user turn，并 SHALL 保存用户实际看到的 final assistant text；一个 repo SHALL 复用 active thread，直至用户执行 `/new`。recent history SHALL 按 context token budget 投影，而不是固定 turn/字符数。

#### Scenario: CLI 重启恢复 thread
- **WHEN** 用户退出后在同一 repo 再次启动 CLI
- **THEN** 系统复用原 active thread
- **AND** ordinary prompt 按当前 context profile 加载 structured checkpoint 与 recent completed turn tail

#### Scenario: 运行时缺失 active thread
- **WHEN** 已初始化 Memory 在 status、turn 写入或 startup recovery 时发现 active thread 缺失
- **THEN** 系统产生 degraded 状态或 typed schema error
- **AND** 不得在首次 bootstrap、`/new` 或显式 clear 之外自动创建 active thread

#### Scenario: 主调用中断
- **WHEN** user turn 已写入但主 LLM 在完成前失败或进程中断
- **THEN** user 原文仍保留
- **AND** turn 被标记 failed 或在下次启动恢复为 interrupted并进入 extraction queue

#### Scenario: 禁止持久化执行 trace
- **WHEN** ordinary 或 repair Agent 产生 reasoning、tool call、observation、synthetic Memory context 或 system prompt
- **THEN** 这些内容不得写入 Memory database 的 RAW turn

#### Scenario: code audit 保存真实完成结果
- **WHEN** `code_audit` 成功完成静态扫描、零 finding 二次复核或一批 finding 处理
- **THEN** 系统向用户显示并保存同一份稳定 workflow summary
- **AND** summary 包含适用的 finding、待审补丁、失败与剩余数量，不保存执行 trace 或 Rich markup

#### Scenario: code audit 扫描失败
- **WHEN** scanner 在创建 audit workspace 前失败
- **THEN** 系统显示具体扫描错误并把 turn 标记 failed
- **AND** 不得将该 turn 以空 assistant text 标记 completed

#### Scenario: patch revise 保存真实完成结果
- **WHEN** `patch_revise` Agent 调用结束并可能留下修订草案
- **THEN** workflow 先完成草案读取、finding binding 校验与当前补丁替换，再向用户显示并保存同一份稳定 summary
- **AND** 成功替换、未生成草案和 binding 拒绝分别保存对应 workflow 结果，不保存 LLM raw answer 或空 `display_answer`
- **AND** 未生成草案与 binding 拒绝是已完成的 workflow 结果；Agent 或 workspace 抛出的异常仍使 turn 进入 failed

### Requirement: `/new` 建立干净工作边界
`/new` SHALL 结束当前 review/runtime 状态、按旧 thread watermark 有界处理 Memory job，并创建新的 active thread，同时仅保留 repo 级 preference 与 decision 可见性。

#### Scenario: watermark 内任务完成
- **WHEN** 用户执行 `/new`
- **THEN** 系统捕获旧 thread 当前 pending job watermark
- **AND** 在 timeout 内循环处理 watermark 以内的 extraction 及其派生 consolidation
- **AND** 随后归档旧 thread并创建空 active thread

#### Scenario: watermark flush 超时
- **WHEN** timeout 到达仍有 watermark 内 job 未完成
- **THEN** 系统显示明确警告并继续创建新 thread
- **AND** 未完成 job 保留在 SQLite，由后台 worker 或重启恢复

#### Scenario: watermark job 暂不可 claim
- **WHEN** captured job 被其他 manager lease，或处于 deadline 前会到期的 retry wait
- **THEN** flush 在 deadline 前有界等待并重新检查 captured job
- **AND** 只有 captured job 及其派生 consolidation 计入 pending，不受其他 thread job 影响

#### Scenario: manager 关闭 watermark 后台处理
- **WHEN** watermark flush 超时后 daemon 仍在处理或等待 captured job，随后 manager 执行 close
- **THEN** manager 停止后续 pipeline step并等待已登记 watermark thread 退出
- **AND** close 返回后该 manager 不再写入 SQLite 或 summary 文件

#### Scenario: 后台 watermark 期间存在新 turn
- **WHEN** watermark flush 超时后仍持有处理锁，且新 thread 已开始一个尚未结束的 turn
- **THEN** 持锁的 flush 在每个 pipeline step 前续租同 owner 的 open turns
- **AND** 普通 worker 因等待处理锁而不能 heartbeat 时，新 turn 仍可在主业务完成后持久化结果

#### Scenario: pending patch 时执行 `/new`
- **WHEN** 当前存在 pending patch 且用户执行 `/new`
- **THEN** 系统终止 pending patch并清除 review workspace 与临时 Agent 状态
- **AND** 不把该 apply/review 结果推断为 durable Memory

#### Scenario: 新 thread 的 Memory 可见性
- **WHEN** `/new` 已创建新 thread
- **THEN** 旧 thread recent history 与 discussion 不再进入 prompt 或检索
- **AND** active project preference/decision 继续可用

#### Scenario: 已开始请求保持 thread binding
- **WHEN** 请求已通过 `begin_turn()` 取得 thread，随后另一控制流执行 `/new`
- **THEN** 已开始请求的 history、Map、search 与 read 继续绑定原 thread
- **AND** `/new` 的 review/history reset 不得清除该请求的 RecallPolicy、调用预算或 readable-ID allowlist
- **AND** 请求结束后清除 request-local binding 和 readable-ID set

### Requirement: durable 两阶段 Memory 处理
每个已结束 turn SHALL 最终由 durable extraction job 处理；合法 candidate SHALL 再由 serialized consolidation job 转为 active semantic revision。无 candidate 是正常成功语义；不可恢复失败 SHALL 进入 terminal `failed`。

#### Scenario: 正常调度 extraction
- **WHEN** pending turn 达到 2 条或最老 pending 达到 30 秒
- **THEN** worker 按 sequence claim 最多 4 条进行 extraction
- **AND** batch 只能包含 attempt/provider-call 计数状态一致的 jobs
- **AND** 执行中新 turn 在当前批结束后继续调度

#### Scenario: extraction 无长期候选
- **WHEN** extraction 得到合法非空 thread checkpoint 但没有 candidate
- **THEN** job 以 succeeded-no-output 完成
- **AND** turn 不会被反复处理

#### Scenario: extraction payload 超过模型容量
- **WHEN** 完整 RAW turn 已持久化，但 extraction primary payload 超过当前 input capacity
- **THEN** LLM payload 依次卸载旧 repair evidence、adjacent turn 与 previous compaction，并对 primary assistant/user 文本使用带标记的首尾投影
- **AND** SQLite 中的 RAW user/assistant text 保持完整
- **AND** 固定 contract 与必需 primary 内容仍无法装入时 job 带 capacity 原因以 succeeded-no-output 结束

#### Scenario: consolidation payload 超过模型容量
- **WHEN** 完整 candidates 与 related active registry 无法同时装入当前 input capacity
- **THEN** 系统不得删除仍参与提交 identity 校验的 active item，也不得调用 provider
- **AND** consolidation job 与全部关联 pending candidates 在同一事务中分别转为 `succeeded_no_output` 与 `suppressed`

#### Scenario: 单纯 apply 不产生记忆
- **WHEN** LLM 提出补丁且用户只执行 `apply`
- **THEN** extraction 不得据此创建 preference、decision 或 repair procedure
- **AND** apply/verification outcome 不进入 durable Memory source

#### Scenario: output contract 一次 repair
- **WHEN** primary 输出违反机器结构或当前 payload 可验证的语义合同
- **THEN** 系统最多执行一次专用 repair
- **AND** repair 成功后才允许 Store 原子提交
- **AND** repair 失败时 extraction 不推进 compaction/candidate，consolidation 则把关联 pending candidates 标记 `suppressed`

#### Scenario: worker 瞬时失败恢复
- **WHEN** primary 遇到允许重试的 provider 或 SQLite 锁错误
- **THEN** job 仅在第一次 claim 后进入一次 `retry_wait`
- **AND** 第二次 claim 再失败时进入 `failed` 并设置 `completed_at`

#### Scenario: runtime schema 损坏使摘要失效
- **WHEN** 后台 heartbeat 或 startup recovery 发现 `user_version`、catalog 或 SQLite consistency 损坏
- **THEN** worker 不发起额外 LLM 调用，并立即将审阅摘要标记为 `stale`
- **AND** `summary_status()` 不得继续返回 `current`

#### Scenario: stale worker 回写
- **WHEN** worker 的 lease owner 或 clear generation 已失效
- **THEN** 该 worker 的结果不得写入 database

### Requirement: Memory 内容必须可追溯且受类型约束
系统 SHALL 只接受项目 `user_preference`、项目 `project_decision` 和 thread `discussion_context`，并 SHALL 验证来源 quote、origin、strength、recall mode 与 applicability 的组合。

#### Scenario: 明确偏好或决定
- **WHEN** candidate 来自用户明确长期表达
- **THEN** 它至少包含一条来自输入 user turn 的精确 quote
- **AND** 可以使用 `strength=hard`；只有明确持续适用时可以使用 `recall_mode=always`

#### Scenario: 中文项目决定生成稳定英文召回短语
- **WHEN** 明确或采纳的 `project_decision` 主要以中文表达，且关键决定语义存在稳定的常用英文检索短语
- **THEN** extraction 的 aliases SHALL 同时覆盖决定对象以及动作、约束或取舍，不得只复制原文中的英文技术名词
- **AND** “旧 memory schema 不兼容，直接删除” SHALL 包含 `backward compatibility`，并 MAY 包含 `delete legacy schema`
- **AND** consolidation SHALL 在 create/revise/supersede 时保留仍适用的 recall metadata，并补足缺失的稳定跨语言表达
- **AND** 确定性 `memory_search("backward compatibility")` SHALL 召回该 active decision

#### Scenario: 用户确认上一轮决策
- **WHEN** 用户用短表达采纳上一轮 assistant 提出的决策
- **THEN** candidate 使用 `origin=adopted_proposal` 并同时引用 assistant 决策与当前 user confirmation
- **AND** 缺少任一来源时 candidate 被拒绝

#### Scenario: 当前局部纠正
- **WHEN** 用户表达“这里”“本次”等当前补丁限制
- **THEN** 系统只建立 runtime patch constraint
- **AND** repair intent 中的局部 clause 不得因截短 source quote 或改写 candidate kind 而被提升为 durable item
- **AND** 只有满足独立 finding 与跨文件证据要求的 `inferred_repetition` 才能推断为 soft preference

#### Scenario: 重复纠正形成 soft preference
- **WHEN** 同一语义纠正在至少三个独立 finding 且至少两个文件中重复出现并无反例
- **THEN** 系统可以创建 `strength=soft`、`origin=inferred_repetition`、`recall_mode=on_match` 的 preference
- **AND** repair review turn SHALL 使用 `<source_scan_id>:<finding_id>` 保存 finding `evidence_key`，extraction SHALL 可读取同 thread 最近最多 20 turn/32K tokens 的 bounded repair evidence
- **AND** 三个不同 user source turn 必须能一对一绑定互不相同的 evidence key，这些来源自身覆盖至少两个 path，且每条 source quote 都与 candidate 语义相交
- **AND** inferred item 不得覆盖 explicit item

#### Scenario: 非重复 candidate 必须引用当前 batch
- **WHEN** candidate 的 `origin` 不是 `inferred_repetition`
- **THEN** 至少一条 user source 必须来自当前 extraction batch 的 turn
- **AND** `adjacent_previous_turn` 或 `recent_repair_evidence` 中的旧 user turn 不能单独支持 `discussion_context`、preference 或 decision

#### Scenario: provenance 受实际 extraction 投影约束
- **WHEN** extraction 因容量卸载 adjacent/evidence source 或裁剪 primary turn 中段
- **THEN** candidate quote 必须同时存在于本次发送给 LLM 的对应 role 文本和 SQLite RAW role 文本
- **AND** 已卸载 source、被省略中段与仅存在于截断标记中的文本不得成为 durable provenance

#### Scenario: 用户提出未决项目方案讨论
- **WHEN** 当前 extraction batch 的 user turn 明确提出尚未形成用户决定、需要后续延续的项目方案取舍
- **THEN** extraction SHALL 创建由当前 user exact quote 支持的 non-factual `discussion_context`
- **AND** assistant 单方面宣称已决定不得生成 `project_decision` 或 `user_preference`
- **AND** assistant 单方结论不得进入该 discussion 的 `subject`、`statement` 或 `content`

#### Scenario: 非法长期内容
- **WHEN** LLM 仅依据 assistant 主张、代码事实、tool result、apply 或推测生成 `user_preference` 或 `project_decision`
- **THEN** 系统拒绝该 candidate
- **AND** `discussion_context` 的代码事实过滤覆盖 `subject`、`statement` 与 `content` 的完整可注入语义

#### Scenario: consolidation identity 与 revision
- **WHEN** consolidation 创建或修订 semantic item
- **THEN** create 的 logical ID 由 Store 生成，revision 只能选择程序提供的 related active item
- **AND** 同 subject/applicability 的 project preference 与 decision 可以进入同一 revision chain
- **AND** 整个 operation set 以单一事务应用，任一非法 ID 使事务回滚

#### Scenario: 同批 create 重复 logical identity
- **WHEN** 同一 consolidation result 的两个 create operation 产生相同 subject/applicability identity
- **THEN** 第一个新 item 立即进入本批 identity registry
- **AND** 第二个 create 被拒绝且整个事务回滚，不产生两个 active chain

#### Scenario: revise 不得制造重复 active identity
- **WHEN** related active registry 同时包含 subject 为 X 的 item A 与 subject 为 Y 的 item B，而 consolidation 返回 `revise(target_id=B, subject=X)`
- **THEN** Store 在继承 B 的 logical ID 前，以规范化 subject/applicability 检查除当前 target 外的 active item
- **AND** 发现 A 后抛出 `MemoryContractError`，不自动改选 A 或替换 A
- **AND** 整个 consolidation transaction 回滚，active revision chain 不新增重复 identity

#### Scenario: bounded related targets 不缩小 identity 校验
- **WHEN** 同一 scope/path 下 active item 超过 related target 的展示上限，且最旧 item 未进入 consolidation payload
- **THEN** Store 仍以完整 active identity 集合校验 create/revise/supersede
- **AND** 发现被展示上限截掉的同 subject/applicability item 时拒绝操作并回滚事务

### Requirement: 无向量渐进检索
系统 SHALL 使用 typed RecallQuery、双通道 Memory Map、规范化 subject/aliases/keywords/path/check-id 和确定性文本匹配进行渐进读取，且 SHALL NOT 依赖 embedding、向量数据库、FTS 或 LLM reranker。

#### Scenario: 自动构建 standing lane
- **WHEN** active item 的 `recall_mode=always` 且 applies-to path 覆盖当前 request scope
- **THEN** item 进入 standing candidate set而不要求 query 词项命中
- **AND** repair policy 仍只允许 preference/decision

#### Scenario: 自动构建 relevant lane
- **WHEN** `on_match` item 与 subject、alias、check-id、keyword 或至少两个不同 content terms 匹配
- **THEN** item 按 path specificity 与确定性 lexical tuple 排序
- **AND** 单独 path match 或 recency 不足以使 item 入选

#### Scenario: 无空格中文自然查询
- **WHEN** 中文 query 在连续汉字中包含已保存的 subject、alias、keyword 或至少两个正文词项
- **THEN** 系统使用 bounded 双字词项建立确定性 lexical match
- **AND** 保留完整中文 term、既有 term 上限与无相关命中时 abstain 的行为

#### Scenario: 搜索 Memory
- **WHEN** Agent 在已 admission 的请求中调用非空 `memory_search`
- **THEN** manager 将 query 与 request base RecallQuery 合并并强制应用 RecallPolicy
- **AND** 显式 search query 的规范化词项在 32 个 query terms 上限内优先于 base finding 与 user text，自动 Map 的既有词项顺序保持不变
- **AND** 每次最多返回八条 active semantic summary
- **AND** 同一请求最多接受四个不同 search query

#### Scenario: 读取 Memory
- **WHEN** Agent 读取本轮 Map 或 search 已暴露的 active ID
- **THEN** 返回 bounded current revision detail 与最新 provenance
- **AND** 同一请求最多读取八个不同 ID，所有结果共享 durable recall token pool

#### Scenario: 无相关命中
- **WHEN** policy 下没有满足 lexical gate 的 item
- **THEN** search 返回空结果且 Map 不以 importance、confidence 或 recency 补位

### Requirement: ordinary 与 repair 上下文隔离
系统 SHALL 让 ordinary 与 repair intent 使用同一 project semantic store，但 SHALL 通过 request-local RecallPolicy 隔离 history、discussion、RAW 与可用 kind。

#### Scenario: ordinary Agent profile
- **WHEN** 执行 `code_explain` 或 `general_chat`
- **THEN** profile 包含 `memory_search` 与 `memory_read`
- **AND** request 可以获得当前 thread recent history、checkpoint、discussion及项目 preference/decision

#### Scenario: repair Agent profile
- **WHEN** 执行 `code_audit`、zero-finding review、`patch_explain` 或 `patch_revise`
- **THEN** profile 包含 `memory_search` 与 `memory_read`
- **AND** Map/search/read 只能返回当前项目、路径适用的 preference/decision
- **AND** 请求不继承 ordinary history、discussion、任意 RAW turn 或其他 repair request trace

#### Scenario: Memory 与 patch evidence 冲突
- **WHEN** Memory statement 与当前用户指令、源码或 finding 不一致
- **THEN** 当前用户指令优先，代码事实以当前源码/finding 为准
- **AND** Memory 不得绕过 source-read、finding binding、focus 或 patch review guardrail

### Requirement: 用户可审计和管理 Memory
CLI SHALL 提供 `/memory status|list|show|forget|clear|export`，并 SHALL 以安全结构诊断显示 retry 与 terminal failed 状态，不持久化模型或 provider 原始内容。

#### Scenario: 查看 Memory job 错误
- **WHEN** Memory 存在 retry 或 terminal failed job
- **THEN** `/memory status` 显示 retry/failed 数量、失败类别、phase、primary/repair、规则和调用计数
- **AND** 普通与 debug 模式均不得显示 prompt、response、user quote、provider body、API key 或私有 endpoint

#### Scenario: 忘记单条 Memory
- **WHEN** 用户执行 `/memory forget <memory-id>`
- **THEN** item 立即退出 routing 与检索并抑制其旧 candidates
- **AND** CLI 明确提示原始 turn 仍被保留

#### Scenario: 清空 Memory
- **WHEN** 用户执行 `/memory clear --confirm`
- **THEN** 系统 fence 旧 worker并删除所有 thread、turn、candidate、item 和 job 数据
- **AND** 创建一个新的空 active thread
- **AND** 既有 export 与 CLI terminal history 不被删除

#### Scenario: bootstrap 损坏后显式清空
- **WHEN** 同一运行中 schema、`memory_meta` 或 active thread 已损坏，用户执行 `/memory clear --confirm`
- **THEN** 系统删除明确的 database/WAL/SHM 工件并创建干净 v4 database
- **AND** 不迁移、不局部修补或读取旧 schema

#### Scenario: JSON export
- **WHEN** 用户执行 `/memory export`
- **THEN** 系统创建不覆盖旧文件的一次性 JSON snapshot
- **AND** snapshot 包含 RAW turns、完整审计关系和安全 job failure diagnostic
- **AND** snapshot 不得包含已拒绝的模型响应或 provider raw error

#### Scenario: 同一时间戳并发 export
- **WHEN** 多个 manager 或线程在相同 export 时间戳并发执行 `/memory export`
- **THEN** 每次成功调用都创建路径唯一且内容完整的 JSON snapshot
- **AND** 不覆盖既有 snapshot，不遗留该次 export 的临时文件或 lock file

### Requirement: `/reset` 不管理 Memory
`/reset` SHALL 只重置项目工作台，并 SHALL 保留 Memory 数据和独立审计工件。

#### Scenario: 重置项目状态
- **WHEN** 用户执行 `/reset`
- **THEN** 系统清理 review workspace、scan、index 与请求缓存
- **AND** 保留 `memory.db`、Memory exports 和 CLI terminal history
- **AND** 界面明确告知用户 Memory 已保留以及清理命令

### Requirement: 退出前执行一次持久任务处理
CLI 退出 SHALL 让当前 pending extraction 与 consolidation job 各获得一次处理机会，但 SHALL NOT 因 retry 或 repair 无限阻塞退出。

#### Scenario: 正常退出且处理成功
- **WHEN** 用户退出且存在 pending Memory job
- **THEN** 系统等待这些 job 的一次处理完成并持久化结果

#### Scenario: 退出处理进入 retry
- **WHEN** job 的第一次 primary 发生允许重试的瞬时错误
- **THEN** 系统保存一次 `retry_wait` 和安全诊断并显示警告
- **AND** CLI 继续退出并在下次启动恢复任务

#### Scenario: 退出处理终态失败
- **WHEN** job 达到调用/claim 上限或发生不可重试错误
- **THEN** 系统保存 terminal `failed` 和安全诊断
- **AND** CLI 不把它继续报告为 pending 或在下次启动重新 claim
