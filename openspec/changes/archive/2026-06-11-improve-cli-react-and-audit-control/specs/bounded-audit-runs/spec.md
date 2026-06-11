## ADDED Requirements

### Requirement: 审计流程有界
Code audit SHALL 在每次用户请求中处理有界数量的 finding；当本轮扫描仍有未处理 finding 时，它 SHALL 提示用户确认当前补丁后重新发起检查继续。

#### Scenario: finding 数量超过 batch limit
- **WHEN** 一次扫描发现的 actionable finding 数量超过 audit batch limit
- **THEN** AutoPatch-J 只处理当前 batch
- **AND** 它会告知用户本轮扫描仍有更多 finding 待处理
- **AND** 它不会承诺跨请求持久化剩余 finding backlog
- **AND** 用户再次发起检查时，AutoPatch-J 会重新扫描当前代码

#### Scenario: 创建 pending patch
- **WHEN** 已处理 finding 产生 patch draft
- **THEN** 这些 draft 保留在 pending review queue 中
