## MODIFIED Requirements

### Requirement: SQLite 持久化普通对话事实
系统 SHALL 使用项目级 `.autopatch-j/memory.db` 作为 thread、turn、Memory job、candidate、semantic item、revision 与 provenance 的唯一运行时事实源，并 SHALL 以 schema v4 的事务和约束保持一致性。合法 bootstrap state SHALL 同时包含唯一 `memory_meta(id=1)` 和唯一 active thread。

#### Scenario: 首次初始化 Memory v4
- **WHEN** 当前 repo 尚无 `memory.db`
- **THEN** 系统创建 `user_version=4` 的干净 SQLite schema
- **AND** 为 repo 创建唯一 active thread

#### Scenario: 拒绝既有非 v4 database
- **WHEN** 既有 `memory.db` 的 `user_version` 不是 4，或 v4 catalog、meta singleton、唯一 active thread 不完整
- **THEN** Memory 进入 degraded 状态且不修改现有文件
- **AND** 不执行 migration、双读或自动删除，只有显式 `/memory clear --confirm` 才创建干净 v4 database

#### Scenario: 拒绝含未知对象的未标版本 database
- **WHEN** `memory.db` 的 `user_version=0` 且已包含未知 schema object
- **THEN** Memory 进入 degraded 状态且不修改现有文件
- **AND** 用户可见诊断准确报告当前支持的 schema version，以及首个未知对象的类型和名称

#### Scenario: 遗留 memory JSON
- **WHEN** 初始化发现 `.autopatch-j/memory.json`
- **THEN** 系统不读取、不迁移且不自动删除该文件
- **AND** 运行时事实源仍只认健康的 v4 database

#### Scenario: 并发写入 turn
- **WHEN** 多个 manager 或 CLI 并发向同一 repo 追加 turn
- **THEN** 每个已提交 turn 均被保留且 sequence 唯一
- **AND** repo 仍只有一个 active thread

#### Scenario: turn scope projection 有界
- **WHEN** 任一 workflow 为新 turn 提供超过 `MAX_SCOPE_PATHS=10` 条 scope path
- **THEN** Store 按输入顺序只持久化前十条并让 extraction payload 使用同一有界投影
- **AND** 实际扫描、Agent focus 与 patch 范围保持完整，不迁移或读取时截断既有 turn
