## Context

参见 `proposal.md`。当前 `LLMClient` 同时承担配置透传、DSML dialect 协商、请求兼容重放和响应容错，导致调用用途无法稳定决定 wire 行为；`ModelContextProfile.max_output_tokens` 又同时被当作 provider 能力、输入预留和应用输出上限。Memory 已有 v4 持久 job、120 秒 lease 与最多三次顶层调用计数，但长 thinking 调用没有续租。

约束包括：继续使用 OpenAI Python SDK 的 Chat Completions；只实现最新行为，不迁移旧配置；流式响应开始后不能安全自动重放；Memory schema v4 不变；真实 LLM 测试不属于默认验证。

## Goals / Non-Goals

**Goals:**

- 用小而明确的 typed provider/purpose profile 决定请求字段和响应协议。
- 分离 provider 能力、应用 input capacity 与 purpose output cap。
- 让一次 `LLMClient.chat()`、一次诊断和一次 Memory `llm_call_count` 对应同一逻辑操作。
- 对 malformed response、finish reason 与 DeepSeek reasoning/tool-call 协议 fail closed。
- 让 Memory 长调用在有效 worker 存活期间持续续租，并保留 clear/generation fencing。

**Non-Goals:**

- 不迁移 OpenAI Responses API，不采用 beta strict tool schema。
- 不支持 DeepSeek 旧模型别名、DSML/Aliyun/Bailian wire 或旧环境变量。
- 不增加跨 provider 的猜测性能力表；未知模型必须显式提供 context 覆盖。
- 不修改 Memory schema 或迁移历史计数。

## Decisions

### 1. Provider 与 purpose 两层 profile

provider resolver 只按精确模型 ID 返回 `DeepSeekV4` 或 `OpenAIChatBaseline`。调用用途继续由 typed `LLMCallPurpose` 表示，purpose profile 提供 stream、thinking、reasoning effort、temperature、response format、timeout 和 output cap；provider profile 把这些意图翻译成合法 wire。

选择这一结构是为了让“模型是谁”和“这次调用做什么”正交，同时避免为两个 provider 引入插件注册系统。未选择 base URL/substring 自动识别，因为它会把代理 endpoint 和同名模型误分类。

### 2. 请求参数只能由代码生成

DeepSeek V4 通过内部 typed 值生成 `extra_body={"thinking":{"type":...}}`，只在 thinking enabled 时发送 `reasoning_effort`，并使用 `max_tokens`。OpenAI baseline 使用 `max_completion_tokens`，且清除 DeepSeek-only message/request 字段。删除 arbitrary `extra_body`、reasoning env 与 stream dialect 配置。

未保留兼容透传，因为项目未上线，继续允许任意 JSON 会绕过 profile 合同并使测试矩阵无限扩张。

### 3. Token 模型显式拆分

`ModelContextProfile` 分别保存 provider context window、provider max output、应用 input capacity 和 operational max output。DeepSeek V4 固定为 1,000,000 / 384,000 / 840,000 / 128,000；classifier 仍以 purpose cap 128 运行。未知模型由 `AUTOPATCH_LLM_CONTEXT_WINDOW` 与 `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS` 提供应用边界。

840K input + 128K operation output + 约 32K margin 恰好形成 1M 应用预算。32K 是安全余量，不是输入或 provider 输出限制。

### 4. 直接标准 parser 与类型化协议错误

删除 dialect registry。parser 累积 stream/nonstream 的 response ID、finish reason、content、字段存在性、reasoning 与 tool call fragments；arguments 完成后必须解码为 JSON object。response model 单独保存 `reasoning_content_present`，从而区分 missing 与 present-empty。

provider profile 在工具执行前验证 DeepSeek thinking tool-call 必须带实际 reasoning 字段。`stop/tool_calls` 正常；资源不足、截断、过滤和其他非法 finish reason 映射为不同 typed errors。React 轨迹保留真实空 content，不制造 `...`。

### 5. SDK 重试即逻辑操作内部 retry

每次 `LLMClient.chat()` 通过 `client.with_options(max_retries=2)` 发起请求，并使用 SDK public raw/streaming response surface 获取 `retries_taken`。SDK 负责连接、timeout、408/409/429/5xx、`Retry-After` 与 backoff；上层删除同输入瞬时 retry 和参数兼容 retry。

changed-input recovery 仍是新逻辑操作：context overflow hard rebuild 一次、compaction fragment 减半一次、Memory durable primary retry、Memory contract repair。流式消费开始后的失败不重放。异常未提供最终 raw response 时 `sdk_retries_taken` 保持 unknown，而不是猜测。

### 6. 分类失败直接进入确定性安全默认

intent 与 pending-review route 都只发一次 `CLASSIFIER` 逻辑操作。失败或非法输出分别返回 `GENERAL_CHAT`、`PATCH_EXPLAIN`、`REVIEW_CONTINUE`，并保留 fallback diagnostic；删除 REACT purpose 的第二次模型分类。

这缩小了失败路径和成本，同时默认选择仍不会绕过 pending review。

### 7. Memory heartbeat 围绕每个 provider 操作

在 Memory primary/repair 调用期间启动单个 daemon heartbeat loop，每 30 秒以同一 owner/generation 续租 job batch，再续租同 owner 的 open turns。provider 返回后先停止 heartbeat 并检查 lease/generation，再决定 repair 或 commit。heartbeat 发现 fencing 后 late result 只能丢弃。

保留 120 秒 lease 作为 worker 进程停止后的恢复边界。`llm_call_count` 在每个顶层 provider 操作前只增加一次；SDK 内部 HTTP attempts 不接触 Store，因此无需 schema migration。

### 8. 前台 RAW debug 与 Memory typed diagnostic 分层

所有 LLM 调用统一记录 purpose、请求策略、SDK 最大重试次数和成功结果的实际重试次数。非 Memory 前台调用在 debug 模式下继续显示最多 20,000 字符的 RAW provider exception/body；Memory primary/repair 无论是否开启 debug，都只暴露异常类、数字 HTTP status、有界 request/response ID、finish reason 及既有受控合同字段，不保存异常 message、provider body、headers、prompt 或 endpoint。

Memory job、status 和 export 是持久诊断边界，provider 错误可能回显 prompt、turn 或认证文本，因此不能沿用前台临时 debug 的 RAW 策略。该分层保持当前实现与 `typed-memory-runtime` 的安全合同，不新增运行时兼容分支。

- 2026-08-01：将原先笼统覆盖所有 LLM 调用的 RAW debug 描述收敛为“非 Memory 前台 RAW、Memory 后台 typed”，原因是验收发现 `llm-call-diagnostics` 与 `typed-memory-runtime` 对 Memory provider 错误的要求互相冲突。

## Risks / Trade-offs

- [未知 OpenAI-compatible provider 不接受 `max_completion_tokens`] → baseline 明确只承诺 OpenAI Chat Completions；未知 provider 需要自行提供兼容 endpoint，不增加猜测性降级。
- [DeepSeek SSE 在首个 event 前发生失败时 SDK 行为依赖 SDK 实现] → 只依赖 SDK 在开始消费前的 retry；应用层不重放 stream。
- [840K token estimator 是保守近似] → preflight 仍保留 provider overflow 后的一次 changed-input hard rebuild。
- [heartbeat thread 遇到 SQLite 短锁竞争] → Store 既有 busy retry 处理短竞争，最终 commit/repair 前的 authoritative lease check 负责 fencing。
- [128K 输出可能产生长延迟和费用] → 这是上限而非目标；classifier 固定 128，prompt 与工具闭环仍应尽早结束。

## Migration Plan

1. 先落地 OpenSpec 与 provider/profile 类型，更新配置解析和依赖。
2. 切换 transport/parser/message adapter，删除 dialect 与 compatibility retry。
3. 切换 classifier、Memory heartbeat 和诊断字段。
4. 更新测试、用户文档及 handoff，运行聚焦与完整确定性回归、OpenSpec strict validation。
5. 回滚时按本 change 的精确文件恢复；Memory schema 未改变，不需要数据回滚。
