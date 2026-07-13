## Purpose

定义 AutoPatch-J 普通对话 Memory 的持久化、线程生命周期、后台处理、渐进检索、上下文隔离及用户审计管理契约。

## Requirements

### Requirement: SQLite 持久化普通对话事实
系统 SHALL 使用项目级 `.autopatch-j/memory.db` 作为普通 thread、turn、Memory job、candidate、item 与 provenance 的唯一运行时事实源，并 SHALL 通过事务和约束保持一致性。合法 bootstrap state SHALL 同时包含唯一 `memory_meta(id=1)` 和唯一 active thread；任一条件缺失都 SHALL 视为 bootstrap 损坏。

#### Scenario: 首次初始化 Memory v2
- **WHEN** 当前 repo 尚无 `memory.db`
- **THEN** 系统创建 `user_version=2` 的 SQLite schema
- **AND** 系统为 repo 创建唯一 active ordinary thread

#### Scenario: 拒绝接管未知未版本化数据库
- **WHEN** 既有 `memory.db` 的 `user_version=0` 且包含任意非 `sqlite_*` 的 table、view、index 或 trigger
- **THEN** 系统进入明确的 degraded 状态，不创建 Memory v2 schema 或修改 `user_version`
- **AND** 既有 schema 与数据保持不变，只有显式 `/memory clear --confirm` 才会删除并重建该数据库

#### Scenario: 拒绝损坏或偏离契约的 Memory v2
- **WHEN** 既有 `memory.db` 的 `user_version=2`，但任一非 `sqlite_*` table、index、view 或 trigger 与 Memory v2 catalog 不一致，或者 `memory_meta` singleton 或唯一 active thread 缺失
- **THEN** 系统进入明确的 degraded 状态，不执行 DDL、seed、`user_version` 写入或 active thread 补建
- **AND** 既有 schema、数据和 journal mode 保持不变，只有显式 `/memory clear --confirm` 才会删除并重建该数据库

#### Scenario: 运行时业务入口发现 bootstrap 损坏
- **WHEN** ordinary admission、history/routing projection、事务型读写、后台 recover/claim/commit、`/new`、list 或 forget 在运行期间发现 bootstrap state 损坏
- **THEN** 对应操作通过 `MemorySchemaError` fail-closed
- **AND** 系统不得把损坏状态伪装成空 Memory、补建 meta 或补建 active thread
- **AND** `status`、`show`、RAW `export` 与显式 `/memory clear --confirm` 仍可用于诊断或恢复

#### Scenario: 并发写入普通 turn
- **WHEN** 多个 manager 或 CLI 并发向同一 repo 追加 turn
- **THEN** 每个已提交 turn 均被保留且 sequence 唯一
- **AND** repo 仍只有一个 active thread

#### Scenario: 遗留 v1 JSON
- **WHEN** v2 初始化发现 `.autopatch-j/memory.json`
- **THEN** 系统直接删除该文件且不读取、不备份、不迁移
- **AND** 删除失败会产生可见的 Memory 存储错误

### Requirement: 普通 thread 跨重启延续
系统 SHALL 在主 LLM 调用前保存 RAW user turn，并 SHALL 保存用户实际看到的 final assistant text；一个 repo SHALL 复用 active thread，直至用户执行 `/new`。

#### Scenario: CLI 重启恢复 thread
- **WHEN** 用户退出后在同一 repo 再次启动 CLI
- **THEN** 系统复用原 active thread
- **AND** ordinary prompt 可以加载其 bounded compaction 和 recent completed turns

#### Scenario: 运行时缺失 active thread
- **WHEN** 已初始化的 Memory 在 status、普通 turn 写入或 startup recovery 时发现 active thread 缺失
- **THEN** 系统产生 degraded 状态或 typed schema error
- **AND** 系统不得在首次 bootstrap、`/new` 或显式 `/memory clear --confirm` 之外自动创建 active thread

#### Scenario: 主调用中断
- **WHEN** user turn 已写入但主 LLM 在完成前失败或进程中断
- **THEN** user 原文仍保留
- **AND** turn 被标记 failed 或在下次启动恢复为 interrupted 并进入 extraction queue

#### Scenario: 禁止持久化执行 trace
- **WHEN** ordinary 或 repair Agent 产生 reasoning、tool call、observation 或 system prompt
- **THEN** 这些内容不得写入 Memory 数据库

### Requirement: `/new` 建立干净工作边界
`/new` SHALL 结束当前工作状态并创建新的 ordinary thread，同时仅保留 repo 级明确偏好和项目决定。

#### Scenario: pending patch 时执行 `/new`
- **WHEN** 当前存在 pending patch 且用户执行 `/new`
- **THEN** 系统终止 pending patch 并清除 review workspace 与临时 Agent 状态
- **AND** 归档旧 ordinary thread并创建空 active thread

#### Scenario: 新 thread 的 Memory 可见性
- **WHEN** `/new` 已创建新 thread
- **THEN** 旧 thread 的 recent history 与 discussion context 不再进入 prompt 或检索结果
- **AND** active user preferences 与 project decisions 继续可用

#### Scenario: 请求执行期间并发切换 thread
- **WHEN** ordinary 请求已通过 `begin_turn()` 取得 thread，随后另一个控制流执行 `/new`
- **THEN** 已开始请求的 history、routing context、`memory_search` 与 `memory_read` 继续绑定 `begin_turn()` 返回的旧 `thread_id`
- **AND** `/new` 只影响之后开始的 ordinary 请求
- **AND** 请求成功或失败结束后都清除该请求的 thread 绑定

### Requirement: durable 两阶段 Memory 处理
每个 ordinary turn SHALL 最终由 durable extraction job 处理；候选 Memory SHALL 再由 serialized consolidation job 转为 active item。

#### Scenario: 正常调度 extraction
- **WHEN** pending turn 达到 2 条或最老 pending 达到 30 秒
- **THEN** worker 按 sequence claim 最多 4 条进行 extraction
- **AND** 执行中新增的 turn 在当前批结束后继续被调度

#### Scenario: worker 失败恢复
- **WHEN** LLM、存储或输出校验失败
- **THEN** job 保存 attempt、最多 20,000 字符的 RAW last error 与 retry time
- **AND** 同一份有界错误写入 Memory 全局状态，供后续状态检查和 export 审计
- **AND** 只要任一带错误的 pending、leased 或 retry-wait job 仍未解决，无关 job 成功不得清空全局 last error
- **AND** 多个 unresolved error 按 job `updated_at DESC, id DESC` 确定全局 last error，最后一个错误 job 解决或 clear 后才清空
- **AND** lease 过期后其他 worker 或下次启动可以继续处理

#### Scenario: stale worker 回写
- **WHEN** worker 的 lease owner 或 clear generation 已失效
- **THEN** 该 worker 的结果不得写入数据库

#### Scenario: extraction 无长期候选
- **WHEN** extraction 得到合法 thread compaction 但没有 candidate
- **THEN** job 以 succeeded-no-output 完成
- **AND** turn 不会被反复处理

### Requirement: Memory 内容必须可追溯且受类型约束
系统 SHALL 只接受 `user_preference`、`project_decision` 和 non-factual `discussion_context`，并 SHALL 验证 LLM 提供的每条来源 quote。

#### Scenario: 明确偏好或决定
- **WHEN** candidate 类型为 user preference 或 project decision
- **THEN** 它至少包含一条来自输入 user turn 的精确 quote
- **AND** 程序验证 quote 是对应 RAW 原文的子串

#### Scenario: 用户确认上一轮提案
- **WHEN** 用户用“同意”等短表达确认上一轮 assistant 提案
- **THEN** decision 可以同时引用 assistant 提案和当前 user 确认
- **AND** 缺少任一来源时该 candidate 不得被接受

#### Scenario: 非法长期内容
- **WHEN** LLM 仅依据 assistant 主张、代码事实、工具结果或推测生成 preference/decision
- **THEN** 系统拒绝该 candidate

#### Scenario: consolidation 原子应用
- **WHEN** consolidation 输出包含任一不存在的 ID 或非法 operation
- **THEN** 整个 consolidation 事务回滚
- **AND** 上一个 active item view 保持不变

### Requirement: 无向量渐进检索
系统 SHALL 使用 routing context、规范化 title/aliases/keywords 和确定性文本匹配进行渐进读取，且 SHALL NOT 依赖 embedding、向量数据库或 FTS5。

#### Scenario: 搜索 Memory
- **WHEN** ordinary Agent 调用 `memory_search` 并提供非空 query
- **AND** 当前 ordinary 请求已经成功完成 `begin_turn()` admission 并绑定 `thread_id`
- **THEN** 系统只搜索 active preferences、active decisions 与 active-thread discussion
- **AND** 只返回存在 exact、prefix、substring 或 content-term 命中的最多 5 条摘要
- **AND** 完整搜索最多执行既有三段 SQLite `SELECT/WITH`，不得为每次搜索追加 bootstrap 完整性查询或逐 item 查询

#### Scenario: 无相关命中
- **WHEN** query 与 active item 没有文本命中
- **THEN** 系统返回空结果
- **AND** 不得以 confidence、importance 或 recency 补足 top-k

#### Scenario: 健康空 Memory
- **WHEN** bootstrap state 健康但当前 thread 没有 completed history、compaction、routing item 或相关检索命中
- **THEN** history 与 search 返回 `[]`，routing context 返回 `""`
- **AND** 这些空结果不得被视为 degraded 状态

#### Scenario: 读取 Memory 证据
- **WHEN** ordinary Agent 使用 `memory_read` 读取 active item
- **THEN** 返回 bounded detail、non-factual 标记和 provenance excerpt
- **AND** archived discussion、forgotten 或 superseded item 不得作为可用记忆返回

### Requirement: ordinary 与 repair 上下文隔离
系统 SHALL 只让 `code_explain` 和 `general_chat` 使用持久 thread、routing context 与 Memory tools；每个 repair 请求 SHALL 使用独立空历史。

#### Scenario: ordinary Agent profile
- **WHEN** 执行 code explain 或 general chat
- **THEN** profile 包含 `memory_search` 与 `memory_read`
- **AND** ordinary initial history 来自 active thread 的 bounded view

#### Scenario: repair Agent profile
- **WHEN** 执行 code audit、zero-finding review、patch explain 或 patch revise
- **THEN** profile 不包含 Memory Context 或 Memory tool schema
- **AND** 请求不继承 ordinary 或其他 repair 请求的消息历史

### Requirement: 用户可审计和管理 Memory
CLI SHALL 提供 `/memory status|list|show|forget|clear|export`，并 SHALL 对删除范围和错误状态给出明确反馈。

#### Scenario: 普通模式查看 Memory 错误
- **WHEN** Memory 已记录后台错误且用户未启用 debug 模式执行 `/memory status`
- **THEN** CLI 提示存在错误并说明可启用 `AUTOPATCH_DEBUG=true` 查看 RAW 详情
- **AND** 普通模式不渲染持久化的 RAW last error

#### Scenario: debug 模式查看 Memory 错误
- **WHEN** Memory 已记录后台错误且用户在 debug 模式执行 `/memory status`
- **THEN** CLI 原样展示已持久化的有界 RAW last error

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
- **WHEN** 同一运行中 `memory_meta` 或 active thread 已缺失，用户执行 `/memory clear --confirm`
- **THEN** 系统以 `max(现存 meta generation, 现存 job generation, 0) + 1` 创建新 generation
- **AND** 删除全部业务数据并重建恰好一个 `memory_meta(id=1)` 和一个 active thread
- **AND** 有效旧 meta 的 `created_at` 被保留，缺失时使用当前时间
- **AND** `last_error` 与 `last_succeeded_at` 被清空，clear 前已 claim 的 batch 不得回写

#### Scenario: RAW export
- **WHEN** 用户执行 `/memory export`
- **THEN** 系统创建不覆盖旧文件的一次性 JSON snapshot
- **AND** snapshot 包含 RAW turn、持久化的 RAW last error 和完整审计关系且不做脱敏

#### Scenario: 同一时间戳并发 RAW export
- **WHEN** 多个 manager 或线程在相同 export 时间戳并发执行 `/memory export`
- **THEN** 每次成功调用都创建路径唯一且内容完整的 JSON snapshot
- **AND** 不覆盖任何既有 snapshot，不因共享临时文件发生随机失败
- **AND** 正常成功或错误返回后不遗留该次 export 的临时文件或 lock file

### Requirement: `/reset` 不管理 Memory
`/reset` SHALL 只重置项目工作台，并 SHALL 保留 Memory 数据和独立审计工件。

#### Scenario: 重置项目状态
- **WHEN** 用户执行 `/reset`
- **THEN** 系统清理 review workspace、scan、index 与请求缓存
- **AND** 保留 `memory.db`、Memory exports 和 CLI terminal history
- **AND** 界面明确告知用户 Memory 已保留以及清理命令

### Requirement: 退出前执行一次持久任务处理
CLI 退出 SHALL 让当前 pending extraction 与 consolidation job 各获得一次处理机会，但 SHALL NOT 因重试而无限阻塞退出。

#### Scenario: 正常退出且处理成功
- **WHEN** 用户退出且存在 pending Memory job
- **THEN** 系统等待这些 job 的一次处理完成并持久化结果

#### Scenario: 退出处理失败或超时
- **WHEN** 某个 job 的本次 LLM 调用失败或达到 timeout
- **THEN** 系统将它保存为 retry 状态并显示警告
- **AND** CLI 继续退出并在下次启动恢复任务
