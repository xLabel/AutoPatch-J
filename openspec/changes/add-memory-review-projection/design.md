## Context

Memory v3 已用项目级 SQLite 保存 thread、checkpoint、active semantic item 与 provenance，并通过 Map/search/read 为 LLM 动态投影。现有 `/memory list/show` 适合逐条诊断，却不能提供一份方便人类浏览的项目全景；`.autopatch-j` 虽被索引忽略，精确路径仍可能绕过索引直接进入 source-read。

## Goals / Non-Goals

**Goals:**

- 从健康的 `memory.db` 生成单文件、人类可读、持续更新的 active Memory 视图。
- 保持 SQLite 单一事实源，让投影失败与 Memory 运行状态相互隔离。
- 用程序侧路径 guard 保证项目状态目录不进入任何 LLM source context。
- 保持刷新成本只与当前 active Memory 成正比，不扫描 RAW 历史。

**Non-Goals:**

- 不把 Markdown 用作 Agent Memory、导入格式、恢复源或 context fallback。
- 不修改 SQLite schema、召回策略、context window、补丁流程或公开文档。
- 不增加文件 watcher、OS 只读权限、跨进程文件锁或多 Agent 一致性。

## Decisions

### 1. 专用 typed snapshot，而不是复用 list/show

`MemoryStore` 在一个显式只读 transaction 中返回 typed snapshot：当前 active thread checkpoint、active project item、当前 thread discussion，以及每项最多三条当前 revision 来源。来源使用批量查询和现有摘录上限，避免 `list_items()` 信息不足和逐项 `show_item()` 的 N+1/无限来源问题。

### 2. 独立 projector 执行整文件原子替换

`MemorySummaryProjector` 只负责确定性渲染、语义指纹、文件完整性检查和同目录临时文件替换；Store 不执行文件 I/O。整文件替换比 Markdown 局部编辑更容易保证分组、删除与 revision 切换的一致性。projector 只读取现有文件的 hash 判断人工修改，不解析正文或回写 SQLite。

### 3. Manager 在已提交状态后触发刷新

启动、成功 extraction、成功 consolidation、forget、clear 和 thread 切换在 SQLite 提交后刷新。后台 worker 与同步 flush 共享同一 hook，避免退出或 `/new` 漏刷新。projection 使用独立进程内 lock；相同语义且文件未被修改时跳过写入。

### 4. Projection 失败不污染 Memory 健康状态

文件失败保留最后成功视图并标为 stale；没有文件时标为 missing。SQLite 操作不回滚，Memory 不进入 degraded。dirty projection 在 worker 中退避重试，手动命令和新的 Memory 事件立即重试。数据库 degraded 时禁止从 Markdown 恢复或重建数据。

### 5. 项目状态目录是 LLM source boundary

`.autopatch-j/**` 在 scope resolution、Agent focus、source-read 工具和底层 SourceReader 四处使用同一归一化路径判断。不能只依赖 prompt、`.gitignore` 或 SymbolIndex，因为精确路径可以绕过索引。正常 Memory Map/search/read 继续从 SQLite 返回数据，不受该文件 guard 影响。

## Risks / Trade-offs

- **[active item 无总量上限，单文件可能增长]** → 不静默截断人类审阅视图；查询与文件大小只随 active item 增长，历史数据不进入投影。
- **[Windows 编辑器可能短暂锁住目标文件]** → 原子替换失败时保留旧文件并退避重试，不阻断 SQLite。
- **[同时运行多个 CLI 可能产生短暂旧视图]** → 当前范围是单 Agent；原子替换保证文件不破碎，后续事件或启动重新物化。
- **[Markdown 内容可能包含结构字符]** → subject 与正文使用安全转义/引用，不能伪造顶层结构。

## Migration Plan

不修改 SQLite schema。健康项目在下一次启动自动创建投影；回滚代码只会留下一个被忽略且不再更新的派生文件，不影响 `memory.db`。

## Open Questions

无。文件位置、内容范围、stale 行为、手动命令和 LLM 隔离均已确认。
