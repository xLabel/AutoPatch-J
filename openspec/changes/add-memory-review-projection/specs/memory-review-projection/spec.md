## ADDED Requirements

### Requirement: 项目 Memory 生成单向人类审阅投影
系统 SHALL 在 `.autopatch-j/memory_summary.md` 生成只供人类审阅的 active Memory 视图，并 SHALL 以健康的项目级 `memory.db` 作为唯一事实源。

#### Scenario: 健康启动重建投影
- **WHEN** CLI 启动且 Memory database 健康
- **THEN** 系统从同一 SQLite read snapshot 生成或校正 `memory_summary.md`
- **AND** 第一行恰为 `<!-- 自动生成，仅供人类审阅；不参与 Memory 处理或 LLM 上下文，Memory 以 memory.db 为准。 -->`

#### Scenario: 投影内容范围
- **WHEN** 系统构建审阅投影
- **THEN** 文件包含当前 thread checkpoint、active project preference/decision 和当前 thread discussion
- **AND** 每项包含完整语义内容、适用范围、召回信号、revision、辅助 ID 与最多三条 bounded current-revision provenance
- **AND** RAW turn、inactive revision、job、诊断与 access count 不得进入文件

#### Scenario: 空 Memory
- **WHEN** 健康 database 尚无 active item 且 checkpoint 为空，或用户完成 Memory clear
- **THEN** 文件保留固定说明和明确的空状态
- **AND** 不保留 clear 前的 Memory 内容

### Requirement: 投影随已提交 Memory 状态更新
系统 SHALL 在投影相关 SQLite 状态提交后刷新完整文件，并 SHALL 使用同目录临时文件原子替换目标。

#### Scenario: 后台物化成功
- **WHEN** extraction 成功更新 checkpoint 或 consolidation 成功更新 active item
- **THEN** 后台 worker 和同步 flush 均刷新投影
- **AND** 相同语义且未被人工修改的文件可以跳过重复写入

#### Scenario: 人工 Memory 操作
- **WHEN** 用户执行 forget、clear 或 `/new`
- **THEN** SQLite 提交后立即刷新投影
- **AND** `/reset` 保留现有投影文件

#### Scenario: 投影文件写入失败
- **WHEN** SQLite 已提交但 Markdown 原子替换失败
- **THEN** Memory database 与 LLM recall 继续正常工作
- **AND** 最后成功文件保留并标记 stale；不存在旧文件时标记 missing
- **AND** 系统在后续 worker、Memory 事件、启动或手动重建时重试

#### Scenario: Database degraded
- **WHEN** Memory database 无法提供可信 snapshot
- **THEN** 系统不得解析旧 Markdown 恢复或提供 Memory
- **AND** 最后成功文件仅作为 stale 人类视图保留

### Requirement: 用户可以手动重建审阅投影
系统 SHALL 提供本地 `/memory summary` 命令，并 SHALL 在 `/memory status` 展示投影状态。

#### Scenario: 手动重建成功
- **WHEN** 用户执行 `/memory summary` 且 database 健康
- **THEN** 系统强制从 SQLite 重建投影
- **AND** 只输出 current 状态、active item 数量和绝对文件路径，不打印全文或调用 Agent

#### Scenario: 查看投影状态
- **WHEN** 用户执行 `/memory status`
- **THEN** 输出包含 projection path、current/stale/missing、最后成功时间和最近错误
- **AND** projection stale 不得把健康的 Memory database 显示为 degraded

### Requirement: 审阅投影不得成为 LLM 输入
系统 MUST NOT 读取 `memory_summary.md` 作为 Memory 处理、LLM request 或上下文重建的输入。

#### Scenario: 正常 Agent 请求
- **WHEN** ordinary 或 repair intent 构建 LLM request
- **THEN** Memory 只能通过 SQLite Map/search/read 和既有 thread projection 进入请求
- **AND** Markdown 独有内容不得出现在请求中

#### Scenario: 后台 Memory LLM
- **WHEN** extraction、consolidation 或 context compaction 调用 LLM
- **THEN** 请求不得读取或附加 `memory_summary.md`

#### Scenario: 人工修改 Markdown
- **WHEN** 人工编辑或删除投影文件
- **THEN** SQLite 与 Agent Memory 保持不变
- **AND** 后续刷新从 SQLite 覆盖或重建文件
