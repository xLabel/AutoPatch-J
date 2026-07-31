## MODIFIED Requirements

### Requirement: durable 两阶段 Memory 处理
每个已结束 turn SHALL 最终由 durable extraction job 处理；合法 candidate SHALL 再由 serialized consolidation job 转为 active semantic revision。无 candidate 是正常成功语义；不可恢复失败 SHALL 进入 terminal `failed`。长 provider 调用期间 SHALL 周期续租当前 job 与同 owner 的 open turns。

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
- **AND** consolidation job 与全部关联 pending candidates在同一事务中分别转为 `succeeded_no_output` 与 `suppressed`

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
- **WHEN** primary 的顶层逻辑操作在 SDK 重试耗尽后仍发生允许 durable retry 的 provider 或 SQLite 锁错误
- **THEN** job 仅在第一次 claim 后进入一次 `retry_wait`
- **AND** 第二次 claim 再失败时进入 `failed` 并设置 `completed_at`

#### Scenario: provider 调用 heartbeat
- **WHEN** Memory primary 或 repair 的 provider 调用持续运行
- **THEN** worker 每 30 秒续租当前 job lease 与同 owner 的 open turns
- **AND** 120 秒 lease 保持为进程停止后的正常恢复边界

#### Scenario: heartbeat 发现 fencing
- **WHEN** heartbeat 发现 lease owner 或 clear generation 已失效
- **THEN** late provider 结果不得触发 repair 或 database commit

#### Scenario: runtime schema 损坏使摘要失效
- **WHEN** 后台 heartbeat 或 startup recovery 发现 `user_version`、catalog 或 SQLite consistency 损坏
- **THEN** worker 不发起额外 LLM 调用，并立即将审阅摘要标记为 `stale`
- **AND** `summary_status()` 不得继续返回 `current`

#### Scenario: stale worker 回写
- **WHEN** worker 的 lease owner 或 clear generation 已失效
- **THEN** 该 worker 的结果不得写入 database
