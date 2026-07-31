## Purpose

定义 AutoPatch-J 如何按精确模型标识选择 provider 行为，并统一约束请求参数、标准 tool-call wire、响应协议、运行预算和单次逻辑操作内的重试语义。

## ADDED Requirements

### Requirement: 精确选择 provider profile
系统 SHALL 只把 `deepseek-v4-flash` 与 `deepseek-v4-pro` 识别为 DeepSeek V4；其他模型 SHALL 使用 OpenAI Chat Completions baseline。

#### Scenario: 选择 DeepSeek V4
- **WHEN** configured model 精确等于 `deepseek-v4-flash` 或 `deepseek-v4-pro`
- **THEN** 请求使用 DeepSeek V4 profile
- **AND** 不通过 base URL、名称子串或别名推断 provider

#### Scenario: 选择 baseline
- **WHEN** configured model 不是上述两个精确标识
- **THEN** 请求使用 OpenAI Chat Completions baseline
- **AND** 请求不包含 DeepSeek `thinking` 或 `reasoning_content`

### Requirement: Provider 请求由调用用途决定
系统 SHALL 以 typed purpose profile 生成 provider 参数，不得接受任意用户 JSON 合并到请求体。

#### Scenario: DeepSeek 分类请求
- **WHEN** purpose 为 `CLASSIFIER`
- **THEN** 请求关闭 thinking、关闭 streaming、输出上限为 128 tokens、temperature 为 0
- **AND** 不发送 `reasoning_effort`

#### Scenario: DeepSeek 推理型请求
- **WHEN** purpose 为 `REACT`、context compaction、Memory primary 或 Memory repair
- **THEN** 请求启用 thinking 并使用 `reasoning_effort=max`
- **AND** REACT 输出上限为 128,000 tokens；compaction 与 Memory 的请求上限不得超过 128,000 tokens

#### Scenario: OpenAI baseline 请求
- **WHEN** provider profile 为 OpenAI baseline
- **THEN** 请求使用 Chat Completions 标准字段和 `max_completion_tokens`
- **AND** 不发送 provider-specific `extra_body`、DeepSeek thinking 字段或严格 beta tool schema

### Requirement: 仅支持标准 OpenAI tool-call wire
系统 SHALL 只生成和解析标准 OpenAI Chat Completions tool-call wire，不得协商 Aliyun、Bailian 或 DSML dialect。

#### Scenario: 构造工具请求
- **WHEN** 请求包含 function tools
- **THEN** 每个工具使用标准 `type=function`、function name、description 和 parameters
- **AND** transport 不因 provider 参数错误删除字段并重放请求

#### Scenario: 非法工具参数
- **WHEN** provider 返回缺失、未完成或非 JSON object 的 tool arguments
- **THEN** 系统在执行工具前返回类型化协议错误
- **AND** 不得静默丢弃该 tool call

### Requirement: 严格保留和校验响应协议
系统 SHALL 从流式与非流式响应保留 response ID、finish reason、content、reasoning 字段存在性和完整 tool calls，并按 finish reason 映射类型化结果。

#### Scenario: reasoning 字段为空但存在
- **WHEN** provider 明确返回空字符串 `reasoning_content`
- **THEN** parsed response 标记该字段存在且值为空字符串
- **AND** 不得与字段缺失合并

#### Scenario: DeepSeek thinking 工具调用缺少 reasoning
- **WHEN** DeepSeek thinking 响应包含 tool calls 但没有 `reasoning_content` 字段
- **THEN** 系统在执行工具前返回类型化协议错误

#### Scenario: finish reason 分类
- **WHEN** finish reason 为 `stop` 或 `tool_calls`
- **THEN** 响应按正常结果处理
- **AND** `insufficient_system_resource` 映射为瞬时 provider 错误，`length` 映射为截断错误，`content_filter` 映射为终态错误

#### Scenario: 流式响应开始后失败
- **WHEN** SSE 已开始消费后发生连接或协议错误
- **THEN** 系统终止本次逻辑操作
- **AND** 不重放已开始的 streaming request

### Requirement: 每次逻辑操作使用统一 SDK 重试
一次 `LLMClient.chat()` SHALL 表示一次顶层逻辑操作，并 SHALL 为该操作配置 SDK `max_retries=2`。

#### Scenario: SDK 可重试错误
- **WHEN** 首次 HTTP attempt 遇到 timeout、connection、408、409、429 或 5xx，且尚未开始消费流
- **THEN** SDK 可在相同逻辑操作内最多重试两次并遵守 provider retry hints
- **AND** 上层不得为同一输入再增加瞬时业务重试

#### Scenario: 成功诊断重试次数
- **WHEN** 一个逻辑操作最终成功
- **THEN** 诊断记录 `sdk_max_retries=2` 和 provider SDK 报告的 `sdk_retries_taken`

#### Scenario: 非重试错误
- **WHEN** provider 返回 HTTP 400 或类型化终态协议错误
- **THEN** SDK/上层不得为兼容参数或相同输入重放请求

### Requirement: 配置面只暴露必要参数
用户配置 SHALL 只暴露 API key、base URL、model 及未知模型所需的 context 覆盖，不得暴露 reasoning、arbitrary extra body 或 stream dialect 开关。

#### Scenario: 读取旧 provider 变量
- **WHEN** 环境包含 `AUTOPATCH_LLM_REASONING_EFFORT`、`AUTOPATCH_LLM_EXTRA_BODY` 或 `AUTOPATCH_LLM_STREAM_DIALECT`
- **THEN** 新运行时不读取或展示这些变量
- **AND** 不提供旧请求行为兼容层
