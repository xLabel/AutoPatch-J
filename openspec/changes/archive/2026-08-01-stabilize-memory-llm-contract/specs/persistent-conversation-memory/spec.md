## MODIFIED Requirements

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

