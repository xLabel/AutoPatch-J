## MODIFIED Requirements

### Requirement: Memory 运行时类型化
Memory manager 内部 SHALL 使用 typed thread、turn、job、candidate、item 与 provenance records 执行业务操作，并 SHALL 通过 SQLite repository 的显式事务边界持久化；裸 dict 只允许存在于严格 LLM JSON contract 和显式 export 边界。

#### Scenario: 为 prompt injection 加载 memory
- **WHEN** 为普通聊天或代码解释构建 Memory routing context
- **THEN** 该 context 来自 typed SQLite records 的 bounded projection
- **AND** 存储损坏或 schema 不支持时系统产生明确 degraded 状态，而不是伪装为空 Memory

#### Scenario: 健康但没有可投影数据
- **WHEN** 唯一 `memory_meta(id=1)` 与唯一 active thread 均存在，但当前 thread 没有相关 history、compaction 或 routing item
- **THEN** typed projection 返回空 history 或空 context
- **AND** 系统保持 healthy，不得把合法空结果误报为 schema error

#### Scenario: 普通 memory 写入
- **WHEN** manager 创建或完成 turn、claim job 或应用 consolidation operation
- **THEN** 它使用 typed records 和 SQLite transaction 执行业务操作
- **AND** 写入失败通过 typed exception 或 typed result 暴露，不得返回含义模糊的布尔值

#### Scenario: 运行时发现 Memory bootstrap state 损坏
- **WHEN** manager 在 status 或 operational guard 中发现唯一 `memory_meta(id=1)` 或唯一 active thread 缺失
- **THEN** status 产生明确 degraded 状态，ordinary admission、projection、事务型读写、后台处理、`/new`、list 与 forget 通过 `MemorySchemaError` 失败
- **AND** manager 不得把损坏状态伪装成 healthy 或自动补建缺失记录

#### Scenario: 诊断与恢复绕过 operational guard
- **WHEN** bootstrap state 已损坏但用户执行 status、show、RAW export 或显式 clear
- **THEN** 这些诊断与恢复路径不被 ordinary operational guard 阻断
- **AND** 只有显式 clear 可以重建缺失的 meta 与 active thread

#### Scenario: 搜索复用 admission 结果
- **WHEN** 已成功 admission 的 ordinary 请求调用 `memory_search`
- **THEN** manager 将该请求绑定的 `thread_id` 直接传给 repository 的三段检索 SQL
- **AND** repository 不为单次搜索增加 bootstrap 查询，完整 catalog manifest 校验仍只发生在初始化

#### Scenario: 显式 JSON export
- **WHEN** 用户执行 `/memory export`
- **THEN** 系统从 typed records 生成一次性 JSON snapshot
- **AND** 该 snapshot 不成为运行时读取源或持续 mirror
