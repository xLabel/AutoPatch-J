## 1. Typed snapshot 与投影器

- [x] 1.1 增加 active Memory 的一致 typed snapshot，并用 bounded 批量查询加载当前 revision provenance。
- [x] 1.2 实现确定性 Markdown renderer、固定首行、语义指纹和 UTF-8 原子替换。
- [x] 1.3 增加 projection current/stale/missing 状态、失败隔离和退避重试。

## 2. 生命周期与 CLI

- [x] 2.1 将启动、两条 pipeline 执行路径、forget、clear 和 thread 切换接入统一刷新入口。
- [x] 2.2 新增 `/memory summary`，扩展 `/memory status`、帮助和补全，并让 `/reset` 保留投影。
- [x] 2.3 在 scope、focus、function tools 和 SourceReader 中禁止 `.autopatch-j/**` 进入 LLM 源码上下文。

## 3. 测试与验证

- [x] 3.1 增加 snapshot、渲染、生命周期、失败恢复和 CLI 的聚焦测试。
- [x] 3.2 增加状态目录精确路径与 Markdown 独有哨兵的 LLM 隔离测试。
- [x] 3.3 运行聚焦测试、完整 pytest、两份 OpenSpec strict validation、`git diff --check`，并确认所有公开文档 hash 未变化。
