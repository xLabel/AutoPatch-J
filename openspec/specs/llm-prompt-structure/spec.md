## Purpose

Define how AutoPatch-J structures natural-language LLM prompts so task boundaries, evidence, raw user input, Memory Context, and machine-readable contracts remain explicit and predictable.

## Requirements

### Requirement: 自然语言 LLM prompt 使用 Markdown section
AutoPatch-J SHALL render natural-language LLM prompts with consistent Markdown sections for system prompts, ReAct user prompts, classifier prompts, and ordinary Memory Context.

#### Scenario: 构建主 Agent system prompt
- **WHEN** constructing a task system prompt
- **THEN** the prompt SHALL use `##` sections for identity, task, tool strategy, output style, forbidden rules, workbench, and optional Memory Context
- **AND** the workbench section SHALL be produced through the same prompt section rendering path as other system sections

#### Scenario: 构建 ReAct user prompt
- **WHEN** constructing a code audit, zero-finding review, code explain, patch explain, or patch revise user prompt
- **THEN** the prompt SHALL separate user input, evidence, execution requirements, code snippets, and patch diff into explicit Markdown sections
- **AND** code snippets and diffs SHALL be fenced when they are included as evidence

### Requirement: 不可信用户输入必须隔离
Prompts that include raw user text SHALL isolate it from system rules and classification rules.

#### Scenario: 意图识别用户输入
- **WHEN** building the intent classifier or review route user prompt
- **THEN** the prompt SHALL include state and rules as Markdown sections
- **AND** raw user text SHALL remain inside a `<<<USER_TEXT ... USER_TEXT` boundary

#### Scenario: Agent 任务用户输入
- **WHEN** building a ReAct user prompt from raw user input
- **THEN** the raw user request or feedback SHALL be placed in its own Markdown section
- **AND** it SHALL NOT be concatenated into the same paragraph as execution rules

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

### Requirement: 机器契约不做 Markdown 化
Machine-readable LLM contracts SHALL keep their native structure instead of being converted to Markdown；Memory extraction、consolidation 与 repair SHALL 使用标准 OpenAI-compatible JSON object mode，并 SHALL 由严格 parser 与 payload-relative validator 执行同一字段合同。

#### Scenario: function call schema
- **WHEN** exporting function call tools to the LLM
- **THEN** tool descriptions and argument descriptions SHALL remain schema descriptions
- **AND** they SHALL NOT be wrapped in Markdown headings

#### Scenario: Memory extraction payload
- **WHEN** sending turn batches to the Memory extraction LLM call
- **THEN** the user payload SHALL remain JSON
- **AND** output SHALL be one exact-key JSON object containing `thread_compaction` and `candidates`
- **AND** `thread_compaction` SHALL always be a trimmed 1..4000 character string, including when `candidates=[]`
- **AND** a non-empty `previous_compaction` SHALL be merged with current visible turns into the rolling checkpoint

#### Scenario: Memory consolidation payload
- **WHEN** sending candidates and related active items to the Memory consolidation LLM call
- **THEN** the user payload SHALL remain JSON
- **AND** output SHALL be one exact-key JSON object containing only allowed operations
- **AND** operations SHALL process every candidate exactly once and use only target IDs visible in the payload

#### Scenario: Memory 标准 JSON mode
- **WHEN** sending Memory primary or repair requests
- **THEN** the request SHALL use standard `response_format={"type":"json_object"}` and standard response parsing
- **AND** it SHALL NOT send provider-specific `extra_body`、Aliyun dialect 或 compatibility retry

#### Scenario: 输出合同 repair
- **WHEN** Memory primary response violates JSON、field、type、length 或 payload-relative semantic rules
- **THEN** the system SHALL perform at most one repair using the same job/batch business input
- **AND** repair SHALL receive only controlled structural violation fields and SHALL regenerate the complete JSON object
- **AND** the first response body、observed field values、exception message 或 provider body SHALL NOT be included
