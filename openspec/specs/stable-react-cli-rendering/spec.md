## Purpose

定义 ReAct 流式事件在 CLI normal/debug 模式下的展示边界，避免 reasoning 和工具输出回归为噪音。

## Requirements

### Requirement: ReAct 渲染保持一致
CLI 渲染 SHALL 在 normal 和 debug 模式下保持 reasoning、tool observation 和 final answer 的视觉一致性。

#### Scenario: normal 模式 tool observation
- **WHEN** normal 模式接收到 reasoning、tool start、observation 和 final answer 事件
- **THEN** reasoning 和 observation 细节会被折叠，且不会泄露冗长工具输出

#### Scenario: debug 模式 tool observation
- **WHEN** debug 模式接收到相同事件序列
- **THEN** 详细 reasoning 和 observation 输出保持可见
