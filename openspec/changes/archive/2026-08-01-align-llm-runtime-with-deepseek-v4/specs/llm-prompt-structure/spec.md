## ADDED Requirements

### Requirement: DeepSeek thinking tool-call 历史保持协议完整
DeepSeek thinking 模式下，assistant tool-call 消息 SHALL 只在 provider 实际返回 `reasoning_content` 字段时回传该字段，并 SHALL 原样保留其字符串值。

#### Scenario: 回传实际 reasoning
- **WHEN** DeepSeek thinking 响应返回 tool calls 和 `reasoning_content`
- **THEN** 后续 assistant tool-call message 包含同一 `reasoning_content` 值
- **AND** 空字符串不得被替换为占位文本

#### Scenario: 不伪造 reasoning
- **WHEN** 非 DeepSeek provider 或响应没有 reasoning 字段
- **THEN** message adapter 不添加 `reasoning_content`
- **AND** DeepSeek thinking tool call 的字段缺失由协议校验在工具执行前拒绝
