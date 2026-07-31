## Why

当前 LLM 层把供应商兼容、用户可注入请求参数、模型 token 能力和应用运行预算混在一起，既会向 DeepSeek V4 发送不兼容字段，也会在解析、重试和诊断中掩盖协议错误。DeepSeek V4 已发布新的模型标识与 1M context 能力，需要把运行时收敛为可审计的 provider profile，并明确应用自身的输入、输出和重试边界。

## What Changes

- 新增 provider profile：仅 `deepseek-v4-flash` 与 `deepseek-v4-pro` 使用 DeepSeek V4 行为，其余模型使用 OpenAI Chat Completions baseline。
- **BREAKING**：移除 `AUTOPATCH_LLM_REASONING_EFFORT`、`AUTOPATCH_LLM_EXTRA_BODY`、`AUTOPATCH_LLM_STREAM_DIALECT` 及 Aliyun/Bailian/DSML dialect；不再接受任意 provider 请求体注入。
- DeepSeek V4 使用官方 `thinking`、`reasoning_effort`、`max_tokens` 和 `reasoning_content` 约定，并按调用用途分配请求参数。
- 将模型能力与应用运行预算拆分：DeepSeek V4 以 1,000,000 token context、384,000 token provider 最大输出为能力边界，AutoPatch-J 使用 840,000 token 输入上限和 128,000 token 推理型操作输出上限。
- 将重试统一为一次顶层逻辑操作内的 OpenAI SDK 重试，记录成功调用实际重试次数；删除参数兼容重试和同输入业务层瞬时重试。
- 严格解析流式/非流式响应、`finish_reason`、tool call 和 reasoning 字段，协议缺失、截断、过滤和资源不足以类型化错误表达。
- 将 debug 诊断按调用类型分层：非 Memory 前台调用可显示有界 RAW provider 错误；Memory 后台调用始终只暴露安全 typed diagnostic，不保存异常 message 或 provider body。
- 两个输入分类器统一使用 `CLASSIFIER` 调用配置，保留确定性的安全回退。
- Memory 保持最多三次顶层逻辑调用语义，并在长调用期间周期续租 job lease 与同 owner 的 open turns，防止合法的 DeepSeek 长推理被误回收。
- 更新配置、诊断、Memory 设计、快速开始、handoff 与依赖版本说明。

## Capabilities

### New Capabilities

- `llm-provider-profiles`: 定义 DeepSeek V4 与 OpenAI Chat Completions baseline 的模型识别、请求构造、协议校验、运行预算和重试边界。

### Modified Capabilities

- `llm-prompt-structure`: 明确 DeepSeek thinking tool-call 消息必须原样回传实际存在的 `reasoning_content`。
- `context-window-management`: 将 provider 模型能力与 AutoPatch-J 输入/输出运行预算分开，并应用 840,000 token 输入上限。
- `llm-call-diagnostics`: 诊断记录 SDK 最大重试次数和成功调用实际发生的 SDK 重试次数，并区分前台 RAW debug 与 Memory typed diagnostic 边界。
- `input-routing-diagnostics`: 两个输入分类器统一使用分类调用配置及确定性失败回退。
- `persistent-conversation-memory`: 长时间 Memory provider 调用期间续租 job 与 open turns，并保持最多三次顶层逻辑调用。
- `typed-memory-runtime`: Memory 的调用次数、失败类型和 fencing 语义按逻辑操作而非 HTTP attempt 计数。

## Impact

- 主要影响 `src/autopatch_j/llm/`、ReAct 消息适配、输入分类、Memory pipeline/store、配置状态输出和相关测试。
- `openai` 依赖调整为 `>=2.32.0,<3`，继续使用 Chat Completions，不迁移 Responses API。
- 删除旧 dialect 和三个环境变量属于有意的配置破坏性变更；项目尚未上线，因此不提供旧配置或旧 wire format 兼容层。
- 不改变 Memory schema v4；现有 `llm_call_count` 值的含义本来就是顶层调用次数，只补充契约和诊断说明。
