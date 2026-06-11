## Context

最初的 change 聚焦在 Memory Context Engine，因此把“Markdown 注入”误收敛到了 Memory 子系统。继续实现时发现，AutoPatch-J 的 prompt 风格问题横跨主 Agent、ReAct user prompt、短 LLM 分类器和 Memory Context。

本次重对齐后，Memory 只是项目级 prompt 标准化的一部分。目标是让所有自然语言 LLM 输入用一致的结构表达任务边界，减少模型把用户输入、证据和系统规则混在一起的概率。

## Goals / Non-Goals

**Goals:**

- 统一自然语言 LLM prompt 的 Markdown section 结构。
- 明确标题、正文、规则、证据和不可信用户输入的边界。
- 保持 Memory JSON 持久化，同时把进入主 LLM 的 Memory 渲染为结构化上下文。
- 保持 function call schema 和 JSON payload 的机器契约属性。
- 保持现有业务流程、工具白名单、补丁队列和 IntentType 分支不变。

**Non-Goals:**

- 不把 function call schema、`ToolArg`、Memory summary payload 改成 Markdown。
- 不要求 LLM 对用户输出 Markdown 报告。
- 不改变补丁修复链路的证据来源。
- 不做旧 prompt 文本结构兼容。

## Decisions

### Markdown section 作为自然语言 prompt 的统一输入结构

自然语言 prompt 使用 `##` 表示主要边界，使用 `###` 表示 section 内部子块。标题只表达结构，不塞具体规则；正文用短句或 bullet 描述任务、规则和限制。

这样可以让 LLM 更稳定地区分“任务是什么”“证据是什么”“用户原文是什么”“哪些规则不可被覆盖”。

### 用户原文必须隔离

意图识别和 review route prompt 保留 `<<<USER_TEXT ... USER_TEXT` 边界。ReAct user prompt 中用户问题、用户反馈和用户原始请求也单独成块。

用户输入是不可信文本，不能和系统规则写在同一个段落里。

### 机器契约保持原格式

function call schema 本身是 JSON schema；`ToolArg` 是参数说明；Memory summary user payload 是 JSON。这些内容不改成 Markdown，否则会增加解析和调用歧义。

### Memory 是结构化上下文，不是 Markdown 核心抽象

Memory 持久化继续使用 JSON。进入主 LLM 前，由程序筛选后渲染为 Markdown 结构化上下文。代码命名不把 `Markdown` 当核心概念，避免误导为 Markdown 驱动的 Memory 系统。

### 2026-06-12：将 memory-only change 重对齐为 project-wide prompt standardization

原 change 名称和文档把目标聚焦在 `enhance-memory-context-engine`，但实际需求已经扩展到整个项目范围的 prompt 标准化。现将 change 重命名为 `standardize-llm-prompt-structure`，并保留原 Memory 任务历史作为早期局部实现。

## Risks / Trade-offs

- [Risk] 过度 Markdown 化污染机器契约。→ 明确排除 function call schema、`ToolArg` 和 JSON payload。
- [Risk] section 过多让 prompt 变长。→ 只对边界明确的内容加标题，正文保持短句和 bullet。
- [Risk] 修改 prompt 影响模型行为。→ 增加结构测试，并保持业务规则和工具白名单不变。
