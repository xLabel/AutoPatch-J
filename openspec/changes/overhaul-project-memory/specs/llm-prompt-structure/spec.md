## MODIFIED Requirements

### Requirement: Memory Context 是普通问答的结构化上下文
Memory SHALL 作为 request-local advisory context 交付给 LLM，而不是拼入 system instruction；ordinary 与 repair intent SHALL 按 typed RecallPolicy 获得不同的 bounded projection，详细 Memory SHALL 通过受控 function tools 渐进读取。

#### Scenario: 注入普通问答 Memory
- **WHEN** constructing prompt context for `code_explain` or `general_chat`
- **THEN** the system SHALL place a bounded thread-checkpoint context before recent history and at most one synthetic Memory Map after recent history、before the current user message
- **AND** thread checkpoint SHALL use session-continuity budget，Memory Map SHALL use durable-recall budget
- **AND** the Map MAY contain current-thread discussion plus project preference/decision and SHALL state that current user instructions take precedence and Memory is not source-code evidence

#### Scenario: 注入 repair Memory
- **WHEN** constructing prompt context for `code_audit`, zero-finding review, `patch_explain`, or `patch_revise`
- **THEN** the synthetic context SHALL contain only path-applicable project `user_preference` and `project_decision`
- **AND** it SHALL NOT contain ordinary history、discussion context 或 arbitrary RAW turn

#### Scenario: Memory context 不成为新 turn
- **WHEN** a synthetic Memory context is sent to the provider
- **THEN** it SHALL NOT be persisted as user RAW text、returned in request trace 或 submitted to Memory extraction
- **AND** the actual current user message SHALL remain the final user-role message before current ReAct output

#### Scenario: 渐进读取详细 Memory
- **WHEN** an Agent needs details beyond the Memory Map
- **THEN** it SHALL use `memory_search` and `memory_read` under the request-bound RecallPolicy
- **AND** each ReAct step SHALL refresh the Map under the same policy，hard rebuild SHALL use a reduced Map target
