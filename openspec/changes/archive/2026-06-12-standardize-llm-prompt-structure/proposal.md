## Why

AutoPatch-J 与 LLM 交互的入口不只 Memory。系统提示词、ReAct 用户任务提示词、意图识别提示词和 Memory 注入上下文都在影响模型如何理解任务、证据、用户原文、工具边界和输出约束。

当前项目已经局部使用 Markdown section，但部分 prompt 仍是普通文本标签和长句拼接，导致边界表达不一致。需要把整个项目范围内的自然语言 LLM prompt 标准化为清晰的 Markdown 结构，而不是只优化 Memory Context。

## What Changes

- 将自然语言 LLM prompt 统一为 Markdown section 结构。
- `##` 用于主要边界，例如任务、工具策略、工作台、用户输入、执行要求、代码证据、补丁差异和 Memory Context。
- `###` 用于 section 内部子块，例如 Memory 中的用户协作偏好、项目画像、相关经历摘要。
- 用户原文必须独立隔离，避免和系统规则混在一起。
- 代码、diff、JSON 证据使用 fenced code block。
- Memory 继续以 JSON 持久化，进入主 LLM 前渲染为 Markdown 结构化上下文。
- function call schema、`ToolArg` 参数说明、Memory summary JSON payload 和历史消息脱水不做 Markdown 化。

## Capabilities

### Modified Capabilities

- `llm-prompt-structure`：标准化 AutoPatch-J 的自然语言 LLM prompt 输入结构，明确哪些内容使用 Markdown section，哪些机器契约保持 JSON/schema。

## Impact

- 影响 `src/autopatch_j/agent/` 下的 system prompt 和 ReAct user prompt。
- 影响 `src/autopatch_j/core/user_input/` 下的短 LLM 分类 prompt。
- 影响 `src/autopatch_j/core/memory/` 下的 Memory Context 注入格式和相关文档表述。
- 不影响 function call schema 的生成方式、不改变工具白名单、不改变 `IntentType` 路由、不改变补丁队列行为。
