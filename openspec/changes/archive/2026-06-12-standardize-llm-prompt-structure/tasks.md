## 1. Memory Schema

- [x] 1.1 重写 typed memory models，加入 working、episodic、semantic、procedural、repo profile 和 maintenance 分区。
- [x] 1.2 更新 normalizer/store，版本不匹配直接返回空 schema，不做旧结构兼容。
- [x] 1.3 更新容量治理，优先裁剪 working/episodic memory，保留治理后的长期 memory。

## 2. Memory Write And Consolidation

- [x] 2.1 将普通问答写入 pending episode，补丁相关 intent 不写入。
- [x] 2.2 更新 summary trigger、summarizer payload 和 prompt，基于 pending episodes 生成 delta。
- [x] 2.3 更新 delta applier，要求 semantic/procedural memory 操作引用合法 source episode ids。

## 3. Prompt Context

- [x] 3.1 将 MemoryPromptContextBuilder 改为结构化 Memory context block。（后续由 6.1 替代扩展：从 memory-only prompt 统一升级为项目级自然语言 prompt 标准化。）
- [x] 3.2 增加轻量相关性评分，按预算注入 procedural、repo profile、semantic、episode、active topic 和 pending user input。
- [x] 3.3 同步 code_explain/general_chat 相关提示词口径，明确 Memory 是普通问答上下文，不是修复证据。

## 4. Tests And Docs

- [x] 4.1 更新 memory 单测覆盖 schema、episode 写入、delta 来源校验、Markdown 注入和容量治理。
- [x] 4.2 更新 docs/memory_design.md，描述新的 Context Engine 设计。
- [x] 4.3 运行聚焦 pytest 和 OpenSpec strict validate。

## 5. 规格重对齐

- [x] 5.1 将 change 从 `enhance-memory-context-engine` 重命名为 `standardize-llm-prompt-structure`。
- [x] 5.2 重写 `proposal.md`，将目标从 Memory 增强纠正为项目级 LLM prompt 结构标准化。
- [x] 5.3 重写 `design.md`，明确 Markdown section、用户输入隔离和机器契约排除规则。
- [x] 5.4 重写 `spec.md`，定义自然语言 prompt、Memory Context 和机器契约的最终行为边界。

## 6. 项目级 Prompt 标准化实现

- [x] 6.1 调整 `MemoryPromptContextBuilder` 和 task system prompt，确保只生成一个顶层 `## Memory Context`。
- [x] 6.2 统一 workbench prompt 的 section 渲染入口。
- [x] 6.3 将 ReAct user prompt 改为 Markdown section，隔离用户输入、证据、执行要求、代码块和 diff。
- [x] 6.4 将意图识别和 review route 的 user prompt 改为 Markdown section，并保留 `<<<USER_TEXT ... USER_TEXT` 边界。
- [x] 6.5 同步 README 和 memory design 中关于 Markdown/结构化 Memory Context 的表述。

## 7. 测试与验证

- [x] 7.1 更新 prompt 相关单测，覆盖 Memory Context 唯一标题、ReAct user prompt section 和 classifier 用户输入边界。
- [x] 7.2 运行聚焦 pytest。
- [x] 7.3 运行 `pytest -q`。
- [x] 7.4 运行 `openspec.cmd validate standardize-llm-prompt-structure --strict`。
