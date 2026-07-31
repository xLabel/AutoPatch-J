## MODIFIED Requirements

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

