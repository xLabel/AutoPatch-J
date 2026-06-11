## Purpose

定义 LLM 调用在 debug 模式下的轻量诊断能力，用于排查调用用途、请求策略和 fallback 路径，同时避免泄露密钥、完整 prompt 或 token 内容。

## Requirements

### Requirement: debug 模式可查看 LLM 诊断
LLM 调用 SHALL 暴露关于调用 purpose 和请求策略的紧凑诊断，且不得暴露密钥或完整 prompt。

#### Scenario: 分类器发生 fallback
- **WHEN** 分类器调用失败并 fallback 到其他策略或默认行为
- **THEN** debug 输出可以显示 purpose 和 fallback 原因
- **AND** fallback 到 REACT 且成功时仍会保留 fallback 原因

#### Scenario: 短调用关闭 reasoning
- **WHEN** classifier 或 memory-summary 调用被发起
- **THEN** 诊断信息显示该调用 purpose 已关闭 reasoning 和 streaming
