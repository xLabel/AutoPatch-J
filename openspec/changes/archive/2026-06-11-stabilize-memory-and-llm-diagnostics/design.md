## 上下文

Memory 持久化有意保持为 JSON。项目已有 memory dataclass 和 normalizer，但 manager、delta applier 和 prompt context 仍在交换原始嵌套 dict。LLM 调用选项已经按 purpose 驱动，但 classifier 和 memory-summary 失败大多是静默的。

## 目标 / 非目标

**目标：**

- 降低 memory schema 意外漂移风险。
- 保持 JSON 文件简单、可检查。
- 让 LLM 请求策略和 fallback 更容易 debug。

**非目标：**

- 增加数据库。
- 为旧 memory 版本增加兼容迁移。
- 在普通模式展示诊断。

## 决策

- 保持 `MemoryStore` 作为唯一 JSON 边界。
- 增加 typed load/save helper，并在 manager-facing 路径中按清晰度收益使用。
- 将 LLM 诊断记录为小型内存记录，不持久化为日志。
- 通过现有 debug-mode CLI 输出展示诊断。

## 风险 / 取舍

- 一次性完整类型化 memory 会过于侵入，因此本次聚焦 manager、prompt injection 和 delta application 边界。
- 诊断信息必须避免泄露 API key 或原始 prompt。
