## 背景原因

Memory 和 LLM 请求行为直接影响回答质量，但当前两者仍暴露较多嵌套 dict 和静默 fallback。这会让回归更难诊断，也会随着 memory 增长提高 schema 漂移风险。

## 变更内容

- 将 memory 内部访问推进到更明确的 typed document，同时将 JSON 持久化保留在 store 边界。
- 增加轻量 LLM 调用诊断，记录调用意图、请求策略和 fallback 错误。
- 诊断信息只在 debug 模式使用，不改变普通用户输出。

## 能力变化

### 新增能力

- `typed-memory-runtime`：memory manager 在写入 JSON 前使用类型化运行时结构。
- `llm-call-diagnostics`：LLM 调用暴露紧凑诊断元数据，服务 debug 可观测性。

### 修改能力

无。

## 影响范围

影响 `core/memory`、`llm`、Agent/CLI debug 输出，以及 memory 和 LLM 聚焦测试。
