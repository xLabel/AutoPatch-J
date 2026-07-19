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

#### Scenario: 当前 revision provenance
- **WHEN** `memory_read` 读取 active revision
- **THEN** detail 优先返回促成本 revision 的 bounded sources
- **AND** 旧 revision sources 保留审计关系但不得挤掉当前 revision evidence

#### Scenario: 运行时发现 Memory bootstrap state 捛坏
- **WHEN** manager 在 status 或 operational guard 中发现唯一 meta、唯一 active thread 或 schema catalog 不合法
- **THEN** status 产生 degraded 状态，Memory admission/projection/search/read/事务写入/后台处理/`/new`/list/forget 通过 `MemorySchemaError` fail-closed
- **AND**主扫描、源码读取和 patch review 可以在显示 Memory unavailable 警告后继续

#### Scenario: 诊断与恢复绕过 operational guard
- **WHEN** bootstrap state 已损坏但用户执行 status、show、RAW export 或显式 clear
- **THEN** 这些诊断与恢复路径不被 ordinary operational guard 阻断
- **AND** 只有显式 clear 可以重建 v3 meta 与 active thread

#### Scenario: request-local readable ID
- **WHEN** Agent 调用 `memory_read`
- **THEN** manager SHALL verify the ID was exposed by this request's Map or a prior policy-filtered search
- **AND** guessed、stale、wrong-thread 或 policy-forbidden ID SHALL fail without returning detail

#### Scenario: 显式 JSON export
- **WHEN** 用户执行 `/memory export`
- **THEN** 系统从 typed records 生成一次性 JSON snapshot
- **AND** 该 snapshot 不成为运行时读取源或持续 mirror
