## Why

项目 Memory 目前只能通过 `/memory list/show` 或 SQLite 工具逐条查看，人类难以快速审阅 Agent 当前保留了什么。需要一份持续更新、可由 `memory.db` 随时重建且绝不进入 LLM 的可读投影。

## What Changes

- 在项目状态目录生成 `.autopatch-j/memory_summary.md`，展示当前 active Memory、当前 thread checkpoint 与精简来源证据。
- SQLite 继续作为唯一事实源；Markdown 只接受单向投影，不提供解析、导入、fallback 或上下文注入。
- 在启动、后台 Memory 成功物化、`/new`、forget、clear 和手动重建后刷新投影，并以原子替换和独立 stale 状态隔离文件失败。
- 新增 `/memory summary`，只返回重建状态、条目数量和文件路径；`/memory status` 展示投影状态。
- 将 `.autopatch-j/**` 设为 LLM focus、源码读取工具和底层 SourceReader 的禁止范围，防止精确路径绕过索引忽略规则。
- 保留 `/reset` 时的 review projection；不修改任何公开文档，不改变 Memory schema、召回、上下文预算或补丁流程。

## Capabilities

### New Capabilities

- `memory-review-projection`: 定义人类可读 Memory 投影的内容、刷新、失败状态、CLI 和 SQLite 单向事实源边界。

### Modified Capabilities

- `safe-source-path-resolution`: 将项目状态目录排除在所有 LLM 可见的 focus 与源码读取路径之外。

## Impact

- 影响 `core/memory/` 的 typed snapshot、manager 生命周期和新 Markdown projector。
- 影响 `/memory` 命令目录、状态渲染、`/reset` 保留规则和项目源码路径 guard。
- 增加聚焦 pytest；不增加第三方依赖、环境变量或 SQLite migration。
