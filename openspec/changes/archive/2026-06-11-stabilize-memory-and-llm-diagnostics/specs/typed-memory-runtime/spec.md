## ADDED Requirements

### Requirement: Memory 运行时类型化
Memory manager 内部 SHALL 在普通操作中使用规范化 typed memory 结构，同时保留 JSON 持久化。

#### Scenario: 为 prompt injection 加载 memory
- **WHEN** 为普通聊天或代码解释构建 memory context
- **THEN** 该 context 来自规范化 memory document
- **AND** 异常或不支持的 memory 文件仍产生空的当前版本 document

#### Scenario: 普通 memory 写入
- **WHEN** manager 追加近期问答或判断摘要触发条件
- **THEN** 它使用 typed memory document 执行业务操作
- **AND** JSON dict 只保留在 store 序列化和 LLM delta applier 边界
