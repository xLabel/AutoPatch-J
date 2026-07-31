## ADDED Requirements

### Requirement: Memory 输出错误与失败诊断类型化
Memory runtime SHALL 区分模型输出合同错误、provider 瞬时/终态错误、SQLite 瞬时错误、schema/corruption、lease fencing 与内部合同错误，并 SHALL 只让模型输出合同错误触发 repair。

#### Scenario: 输出结构诊断
- **WHEN** parser 或 payload-relative validator 拒绝模型输出
- **THEN** typed diagnostic 只包含受控 phase、call stage、field path、rule、observed JSON type/length、白名单 recognized/missing keys、response ID 与 finish reason
- **AND** field path 和 key 名必须来自程序合同，不得复制未知模型 key 或字段值

#### Scenario: provider 失败诊断
- **WHEN** Memory provider 调用抛出异常
- **THEN** typed diagnostic 最多记录异常类、数字 HTTP status 和裁剪后的 request/response ID
- **AND** 不记录异常 message、response body、headers、prompt 或 endpoint

#### Scenario: status 区分可用与降级
- **WHEN** SQLite runtime 可读但存在 terminal failed job
- **THEN** `MemoryStatus` 报告 `healthy=true`、`degraded=true`、`failed_jobs>0` 和 typed last failure
- **AND**既有 active items、thread history 与核心修复工作流保持可用

#### Scenario: provider 调用计数持久化
- **WHEN** worker 即将发送 Memory primary 或 repair
- **THEN** Store 在有效 lease 下原子增加该 batch 每个 job 的 provider call count
- **AND** 任一 job 达到三次后不得再发请求

## MODIFIED Requirements

### Requirement: Memory 运行时类型化
Memory manager 内部 SHALL 使用 typed thread、turn、job、candidate、semantic item、revision、provenance、RecallQuery、RecallPolicy 与 Memory Map records 执行业务操作，并 SHALL 通过 SQLite repository 的显式事务边界持久化；裸 dict 只允许存在于严格 LLM JSON contract、provider wire format 和显式 export 边界。

#### Scenario: 为 prompt injection 召回 Memory
- **WHEN** 任一 Agent intent 构建 request context
- **THEN** manager 根据 typed RecallQuery 与 RecallPolicy 返回 bounded Memory Map
- **AND** repair policy 的 repository projection 只能包含 project preference/decision
- **AND** 存储损坏或 schema 不支持时产生明确 degraded 状态，而不是伪装为空 Memory

#### Scenario: 健康但没有可投影数据
- **WHEN**唯一 `memory_meta(id=1)` 与唯一 active thread 均存在，但 policy 下没有相关 history、checkpoint 或 semantic item
- **THEN** typed projection 返回空 history、空 Map 或空 search result
- **AND** 系统保持 healthy，不得用 recency 填充或误报 schema error

#### Scenario: degraded ordinary history
- **WHEN** ordinary turn admission 已因 schema、corruption 或 storage error 显示 degraded notice
- **THEN** 后续 thread history 读取返回空投影且主 Agent 继续执行
- **AND** `MemoryContractError` 与非 Memory 编程错误仍显式失败

#### Scenario: thread checkpoint 读取降级
- **WHEN** Memory request 已成功打开，但 active thread checkpoint 读取发生 `MemoryStorageError`
- **THEN** 系统不得绑定半完成的 request state，并以空 checkpoint 与空 Memory Map 继续主 Agent
- **AND** `MemoryContractError` 与非 Memory 编程错误仍显式失败

#### Scenario: typed semantic 写入
- **WHEN** manager 创建/完成 turn、claim job、验证 candidate 或应用 consolidation operation
- **THEN** 它使用 typed records 和 SQLite transaction 执行业务操作
- **AND** item 明确保存 subject、statement、strength、origin、recall mode、applicability、Store-owned logical identity 与 revision
- **AND** 写入失败通过 typed exception 或 typed result 暴露

#### Scenario: 主业务后的 Memory turn 收尾降级
- **WHEN** 主扫描、源码读取或补丁操作已经成功，但完成 turn 时发生 storage、lease、not-found 或 thread-conflict Memory error
- **THEN** 系统显示 Memory degraded notice 并保留已经成功的主业务结果
- **AND** 主业务本身失败时，Memory 收尾故障不得覆盖原异常
- **AND** `MemoryContractError` 与非 Memory 编程错误继续显式失败

#### Scenario: 失败收尾发生 contract error
- **WHEN** 主业务已抛出异常且 `fail_turn()` 又抛出 `MemoryContractError`
- **THEN** 顶层继续抛出原始业务异常并将 Memory contract error 保留为 cause
- **AND** 不得用次生 Memory 错误替换扫描、provider 或补丁异常

#### Scenario: 当前 revision provenance
- **WHEN** `memory_read` 读取 active revision
- **THEN** detail 优先返回促成本 revision 的 bounded sources
- **AND** 旧 revision sources 保留审计关系但不得挤掉当前 revision evidence

#### Scenario: memory read 错误分类
- **WHEN** 已 admission 请求调用 `memory_read` 但读取失败
- **THEN** `MemoryNotFoundError` 只映射为该 ID 不存在或当前不可读取，并且不返回 detail
- **AND** `MemoryStorageError` 及其 schema/corruption 子类产生明确 degraded notice，同时主 ReAct 请求继续
- **AND** `MemoryContractError` 明确报告调用额度或 durable recall token pool 约束，不伪装为未找到，也不标记为 storage degraded
- **AND** 其他异常不得映射为“Memory 不存在”

#### Scenario: memory search 错误分类
- **WHEN** 已 admission 请求执行 `memory_search` 但存储、schema/corruption 或请求 contract 发生错误
- **THEN** storage/schema/corruption 错误产生 `degraded_notice()`，contract 错误明确说明 policy/额度约束
- **AND** unexpected exception 不得被改写成“policy 或额度拒绝”，也不得伪装为 Memory 缺失

#### Scenario: 运行时发现 Memory bootstrap state 捛坏
- **WHEN** manager 在 status 或 operational guard 中发现唯一 meta、唯一 active thread 或 schema catalog 不合法
- **THEN** status 产生 degraded 状态，Memory admission/projection/search/read/事务写入/后台处理/`/new`/list/forget 通过 `MemorySchemaError` fail-closed
- **AND**主扫描、源码读取和 patch review 可以在显示 Memory unavailable 警告后继续

#### Scenario: 运行期间 schema catalog 被修改
- **WHEN** 已初始化 Store 发现 `user_version` 不再为 4，或 SQLite schema cookie 已变化
- **THEN** operational/status guard 校验完整 v4 catalog、quick check、foreign key 与 bootstrap state
- **AND** 非法 catalog 立即 fail-closed，status 不得硬编码报告 healthy v4

#### Scenario: summary projection 执行运行时校验
- **WHEN** 已初始化 Store 在生成 summary snapshot 前发现 user version、catalog 或 bootstrap state 已变为非法
- **THEN** snapshot 在同一 read transaction 中通过 runtime guard fail-closed
- **AND** summary refresh 标记 stale，不得把不受支持的 database 继续投影为 `current`

#### Scenario: worker operational guard 发现损坏
- **WHEN** worker 的 heartbeat 或 startup recovery 抛出 `MemorySchemaError` 或 `MemoryCorruptError`
- **THEN** manager 立即调用 summary stale 边界并保留 worker 重试能力
- **AND** 瞬时锁竞争仍可等待下一轮，不创建新的后台线程或恢复路径

#### Scenario: 诊断与恢复绕过 operational guard
- **WHEN** bootstrap state 已损坏但用户执行 status、show、RAW export 或显式 clear
- **THEN** 这些诊断与恢复路径不被 ordinary operational guard 阻断
- **AND** 只有显式 clear 可以重建 v4 meta 与 active thread

#### Scenario: clear 重建运行时损坏 catalog
- **WHEN** 已初始化 Store 的 user_version、catalog 或 SQLite consistency 在运行期间变为非法，用户显式执行 clear
- **THEN** manager 停止并等待自身后台线程，删除明确的 database/WAL/SHM 工件并创建干净 v4 catalog
- **AND** 不迁移、不局部修补或保留损坏 schema object

#### Scenario: request-local readable ID
- **WHEN** Agent 调用 `memory_read`
- **THEN** manager SHALL verify the ID was exposed by this request's Map or a prior policy-filtered search
- **AND** guessed、stale、wrong-thread 或 policy-forbidden ID SHALL fail without returning detail

#### Scenario: 显式 JSON export
- **WHEN** 用户执行 `/memory export`
- **THEN** 系统从 typed records 生成一次性 JSON snapshot
- **AND** 该 snapshot 不成为运行时读取源或持续 mirror
