## MODIFIED Requirements

### Requirement: Memory 输出错误与失败诊断类型化
Memory runtime SHALL 区分模型输出合同错误、provider 瞬时/截断/终态错误、SQLite 瞬时错误、schema/corruption、lease fencing 与内部合同错误，并 SHALL 只让模型输出合同错误及 `length` 截断触发最多一次受控 repair。

#### Scenario: 输出结构诊断
- **WHEN** parser 或 payload-relative validator 拒绝模型输出
- **THEN** typed diagnostic 只包含受控 phase、call stage、field path、rule、observed JSON type/length、白名单 recognized/missing keys、response ID 与 finish reason
- **AND** field path 和 key 名必须来自程序合同，不得复制未知模型 key 或字段值

#### Scenario: provider 失败诊断
- **WHEN** Memory provider 调用抛出异常
- **THEN** typed diagnostic 最多记录异常类、数字 HTTP status 和裁剪后的 request/response ID
- **AND** 不记录异常 message、response body、headers、prompt 或 endpoint

#### Scenario: Memory length 截断
- **WHEN** Memory primary response 的 finish reason 为 `length`
- **THEN** 系统可使用同一业务输入执行一次受控完整合同 repair
- **AND** 其他 purpose 的 `length` 直接返回类型化截断错误

#### Scenario: status 区分可用与降级
- **WHEN** SQLite runtime 可读但存在 terminal failed job
- **THEN** `MemoryStatus` 报告 `healthy=true`、`degraded=true`、`failed_jobs>0` 和 typed last failure
- **AND** 既有 active items、thread history 与核心修复工作流保持可用

#### Scenario: provider 逻辑调用计数持久化
- **WHEN** worker 即将发送 Memory primary 或 repair
- **THEN** Store 在有效 lease 下原子增加该 batch 每个 job 的 `llm_call_count`
- **AND** 同一 `LLMClient.chat()` 内部的 SDK retry 不重复增加计数，任一 job 达到三次逻辑调用后不得再发请求
