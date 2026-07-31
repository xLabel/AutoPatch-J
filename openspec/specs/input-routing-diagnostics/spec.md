## Purpose

记录 AutoPatch-J 路由和意图分类 fallback 的可观测性契约，确保分类器失败时仍沿用安全默认行为，同时让 debug 模式能解释 fallback 来源与原因。

## Requirements

### Requirement: 路由 fallback 诊断
当 route 或 intent 分类失败时，AutoPatch-J SHALL 只使用一次 `CLASSIFIER` 逻辑操作并保留安全的确定性 fallback，同时 SHALL 让 fallback 原因可用于 debug 模式展示。

#### Scenario: 无待确认补丁时意图分类器失败
- **WHEN** `CLASSIFIER` 在没有 pending patch 时失败或返回非法 intent
- **THEN** 输入路由到 `GENERAL_CHAT`
- **AND** 不发起 REACT fallback 调用，debug 诊断包含失败原因

#### Scenario: pending intent 分类器失败
- **WHEN** pending patch 的 intent classifier 失败或返回非法 intent
- **THEN** 输入路由到 `PATCH_EXPLAIN`
- **AND** 不发起 REACT fallback 调用，debug 诊断包含失败原因

#### Scenario: pending review 路由分类器失败
- **WHEN** pending-review route classifier 失败或返回非法 route
- **THEN** 输入默认使用 `REVIEW_CONTINUE`
- **AND** 不发起 REACT fallback 调用，debug 诊断包含失败原因
