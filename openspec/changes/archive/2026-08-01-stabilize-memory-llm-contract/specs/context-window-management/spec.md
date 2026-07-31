## ADDED Requirements

### Requirement: Memory provider 调用具有持久化硬上限
系统 SHALL 对每个 Memory extraction 或 consolidation job 持久化 provider 调用计数，并 SHALL 将 primary、job retry 与 output repair 的总调用数限制为最多三次。

#### Scenario: primary 首次发生瞬时失败
- **WHEN** Memory primary 遇到 timeout、connection、HTTP 408/429/5xx 或 SQLite busy/locked
- **THEN** job 最多进入一次 `retry_wait`
- **AND** SDK 不得在单次业务调用内部执行隐藏重试

#### Scenario: 第二次 primary 输出仍违反合同
- **WHEN** job 已因瞬时失败执行第二次 primary，且返回可解析但违反输出合同的响应
- **THEN** 系统最多执行一次 repair，作为该 job 的第三次且最后一次 provider 调用
- **AND** repair 失败后不得再次 claim 或调用 provider

#### Scenario: repair 前 lease 续期
- **WHEN** primary 输出违反合同且 job 仍持有有效 lease
- **THEN** 系统在 repair 前把 lease 续至完整 120 秒
- **AND** lease 或 generation 已失效时不得调用 repair 或写回 stale 结果

#### Scenario: repair envelope 超过容量
- **WHEN** primary 已返回非法输出，但完整 repair contract 与同一业务 payload 无法装入 input capacity
- **THEN** job 直接进入 terminal `failed`
- **AND** 系统不得删除 consolidation active registry、再次缩短不可丢 primary 内容或发起 repair

