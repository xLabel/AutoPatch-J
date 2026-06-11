## 1. Memory 运行时形态

- [x] 1.1 增加 typed memory document 的 load/save helper。
- [x] 1.2 在 prompt context 和 manager 路径中尽量使用 typed access。
- [x] 1.3 将 JSON dict 转换保留在 store 和 LLM delta 边界。

## 2. LLM 诊断

- [x] 2.1 增加紧凑 LLM 诊断记录，且不改变 `LLMClient.chat` 输出语义。
- [x] 2.2 记录 classifier fallback 和请求选项策略。
- [x] 2.3 通过现有 CLI debug 输出暴露 debug-mode 诊断。

## 3. 验证

- [x] 3.1 运行 memory 和 LLM 聚焦测试。
- [x] 3.2 校验该 OpenSpec change。

## 4. 文档规则对齐

- [x] 4.1 按 `AGENTS.md` 铁律将 proposal、design、spec 和 tasks 正文化为中文。
- [x] 4.2 运行 OpenSpec strict validate。

## 5. 验证发现后的修复

- [x] 5.1 保留 classifier fallback 到 REACT 且成功时的 debug 诊断原因。
- [x] 5.2 将普通 memory 追加和摘要触发判断推进到 typed memory document。
- [x] 5.3 更新 llm-call-diagnostics 和 typed-memory-runtime specs，明确修复后的行为边界。
- [x] 5.4 增加聚焦测试并运行 OpenSpec strict validate。

## 6. 归档前规格同步

- [x] 6.1 将 llm-call-diagnostics 和 typed-memory-runtime delta specs 同步到 openspec/specs/ 主规格目录。
