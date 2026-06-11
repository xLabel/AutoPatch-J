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
Memory SHALL remain JSON at rest and SHALL be rendered as a bounded Markdown-structured context only for `code_explain` and `general_chat`.

#### Scenario: 注入普通问答 Memory
- **WHEN** constructing prompt context for `code_explain` or `general_chat`
- **THEN** the system SHALL inject one top-level `## Memory Context` section when relevant memory exists
- **AND** selected memory categories SHALL appear as subsection content under that section
- **AND** the section SHALL state that Memory is not source-code evidence

#### Scenario: 补丁流程不注入 Memory
- **WHEN** constructing prompt context for `code_audit`, `patch_explain`, or `patch_revise`
- **THEN** the memory context SHALL be empty

### Requirement: 机器契约不做 Markdown 化
Machine-readable LLM contracts SHALL keep their native structure instead of being converted to Markdown.

#### Scenario: function call schema
- **WHEN** exporting function call tools to the LLM
- **THEN** tool descriptions and argument descriptions SHALL remain schema descriptions
- **AND** they SHALL NOT be wrapped in Markdown headings

#### Scenario: Memory summary payload
- **WHEN** sending consolidation input to the memory summary LLM call
- **THEN** the user payload SHALL remain JSON
- **AND** the summary output contract SHALL continue to require a JSON object
