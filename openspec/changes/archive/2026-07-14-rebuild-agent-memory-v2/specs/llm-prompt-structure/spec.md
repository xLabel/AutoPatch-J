## MODIFIED Requirements

### Requirement: Memory Context 是普通问答的结构化上下文
Memory SHALL 以 SQLite records 持久化，并 SHALL 仅为 `code_explain` 和 `general_chat` 渲染 bounded Markdown routing context；详细 Memory SHALL 通过受控 function tools 渐进读取。

#### Scenario: 注入普通问答 Memory
- **WHEN** constructing prompt context for `code_explain` or `general_chat`
- **THEN** the system SHALL inject at most one top-level `## Memory Context` section when relevant routing data exists
- **AND** the section SHALL contain bounded thread compaction、active explicit preferences、active project decisions 与 active-thread discussion index
- **AND** the section SHALL state that Memory is not source-code evidence and current user instructions take precedence

#### Scenario: 渐进读取详细 Memory
- **WHEN** ordinary Agent needs details beyond the routing context
- **THEN** it SHALL use `memory_search` and `memory_read` instead of receiving arbitrary top-k item bodies automatically

#### Scenario: 补丁流程不注入 Memory
- **WHEN** constructing prompt context for `code_audit`, zero-finding review, `patch_explain`, or `patch_revise`
- **THEN** the memory context SHALL be empty
- **AND** the tool schema SHALL NOT include Memory tools

### Requirement: 机器契约不做 Markdown 化
Machine-readable LLM contracts SHALL keep their native structure instead of being converted to Markdown.

#### Scenario: function call schema
- **WHEN** exporting function call tools to the LLM
- **THEN** tool descriptions and argument descriptions SHALL remain schema descriptions
- **AND** they SHALL NOT be wrapped in Markdown headings

#### Scenario: Memory extraction payload
- **WHEN** sending turn batches to the Memory extraction LLM call
- **THEN** the user payload SHALL remain JSON
- **AND** the output contract SHALL require one JSON object containing thread compaction and candidates

#### Scenario: Memory consolidation payload
- **WHEN** sending candidates and related active items to the Memory consolidation LLM call
- **THEN** the user payload SHALL remain JSON
- **AND** the output contract SHALL require one JSON object containing only allowed operations
