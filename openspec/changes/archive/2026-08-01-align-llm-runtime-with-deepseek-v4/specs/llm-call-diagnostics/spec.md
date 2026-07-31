## MODIFIED Requirements

### Requirement: 开发者 debug 模式可查看有界 RAW LLM 诊断
LLM 调用 SHALL 暴露调用 purpose、provider 请求策略、`sdk_max_retries` 与成功结果的 `sdk_retries_taken`；普通模式 SHALL 保持简洁且不得渲染 RAW 错误。`AUTOPATCH_DEBUG=true` 时，非 Memory 前台调用 SHALL 展示最多 20,000 字符的 RAW provider exception、error body 和 response body；Memory primary/repair SHALL 始终只暴露安全 typed diagnostic。系统 SHALL NOT 主动从 request messages、prompt、headers 或认证配置附加额外内容；对于非 Memory 前台 RAW exception/body，系统 SHALL NOT 对其自身做敏感信息扫描或脱敏。

#### Scenario: 分类器发生 fallback
- **WHEN** 分类器调用失败并使用确定性默认行为
- **THEN** debug 输出显示 `CLASSIFIER` purpose、fallback 原因和有界 RAW provider 错误
- **AND** 不发起 REACT fallback 调用

#### Scenario: 普通模式发生 LLM 失败
- **WHEN** 任意 LLM 调用失败且未启用 debug 模式
- **THEN** 面向用户的 CLI 输出不渲染 RAW exception 或 provider body
- **AND** 仍可以给出简洁的失败或 fallback 状态

#### Scenario: debug RAW 包含 markup-like 文本
- **WHEN** 非 Memory 前台调用的 provider exception 或 body 包含 `[/]`、`[bold]` 或其他 Rich markup-like 文本
- **THEN** CLI 将这些内容按 literal text 原样渲染
- **AND** 渲染层不得抛出二次异常或掩盖原 provider failure

#### Scenario: purpose 请求策略
- **WHEN** 任一 LLM 调用被发起
- **THEN** 诊断显示该 purpose 的 reasoning、streaming、output cap 与 `sdk_max_retries=2`
- **AND** 成功完成时显示 SDK 报告的 `sdk_retries_taken`

#### Scenario: Memory 后台调用失败
- **WHEN** Memory extraction/consolidation primary 或 repair 调用失败或 timeout
- **THEN** 诊断记录 purpose、请求策略、异常类、数字 HTTP status，以及存在时的有界 request/response ID 与 finish reason
- **AND** 即使启用 debug，也不记录异常 message、provider body、headers、prompt 或 endpoint
- **AND** Memory job、status、export 与 session debug summary 均不得包含 provider RAW 错误
