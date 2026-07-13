## MODIFIED Requirements

### Requirement: 开发者 debug 模式可查看有界 RAW LLM 诊断
LLM 调用 SHALL 暴露调用 purpose 和请求策略；普通模式 SHALL 保持简洁且不得渲染 RAW 错误，`AUTOPATCH_DEBUG=true` 时 SHALL 展示最多 20,000 字符的 RAW provider exception、error body 和 response body。系统 SHALL NOT 主动从 request messages、prompt、headers 或认证配置附加额外内容，但 SHALL NOT 对 exception/body 自身做敏感信息扫描或脱敏。

#### Scenario: 分类器发生 fallback
- **WHEN** 分类器调用失败并 fallback 到其他策略或默认行为
- **THEN** debug 输出显示 purpose、fallback 原因和有界 RAW provider 错误
- **AND** fallback 到 REACT 且成功时仍会保留 fallback 原因

#### Scenario: 普通模式发生 LLM 失败
- **WHEN** 任意 LLM 调用失败且未启用 debug 模式
- **THEN** 面向用户的 CLI 输出不渲染 RAW exception 或 provider body
- **AND** 仍可以给出简洁的失败或 fallback 状态

#### Scenario: debug RAW 包含 markup-like 文本
- **WHEN** provider exception 或 body 包含 `[/]`、`[bold]` 或其他 Rich markup-like 文本
- **THEN** CLI 将这些内容按 literal text 原样渲染
- **AND** 渲染层不得抛出二次异常或掩盖原 provider failure

#### Scenario: 短调用关闭 reasoning
- **WHEN** classifier、memory-extraction 或 memory-consolidation 调用被发起
- **THEN** 诊断信息显示该调用 purpose 已关闭 reasoning 和 streaming

#### Scenario: Memory 后台调用失败
- **WHEN** memory-extraction 或 memory-consolidation 调用失败或 timeout
- **THEN** 诊断记录 purpose、请求策略和最多 20,000 字符的 RAW exception/provider body
- **AND** 系统不主动追加 request prompt、turn 正文、headers 或认证配置
- **AND** provider 返回内容自身包含的文本按 RAW 诊断保留
