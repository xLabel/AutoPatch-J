## Purpose

记录 AutoPatch-J 路由和意图分类 fallback 的可观测性契约，确保分类器失败时仍沿用安全默认行为，同时让 debug 模式能解释 fallback 来源与原因。

## Requirements

### Requirement: 路由 fallback 诊断
当 route 或 intent 分类失败时，AutoPatch-J SHALL 保留现有安全 fallback 行为，并 SHALL 让 fallback 原因可用于 debug 模式展示。

#### Scenario: 无待确认补丁时意图分类器失败
- **WHEN** 短 LLM 意图分类器在没有 pending patch 时失败
- **THEN** 输入使用现有 general-chat fallback 进行路由
- **AND** debug 诊断包含 LLM 分类器失败且已使用 fallback 的信息

#### Scenario: pending review 路由分类器失败
- **WHEN** pending-review 路由分类器无法产出有效 route
- **THEN** 输入默认继续 pending review
- **AND** debug 诊断包含 fallback 原因
