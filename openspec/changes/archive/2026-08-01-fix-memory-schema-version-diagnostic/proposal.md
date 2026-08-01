## Why

Memory 当前使用 schema v4，但拒绝含未知对象的 `user_version=0` 数据库时，degraded 诊断仍声称系统拒绝静默升级到 v3。该错误会通过 `/memory status` 等诊断路径暴露给用户，容易造成错误的恢复判断。

## What Changes

- 诊断中的目标 schema version 改为跟随运行时的 `MEMORY_SCHEMA_VERSION`。
- 为未知未标版本数据库补充用户可见错误信息测试，确保当前版本和未知对象身份均可追踪。
- 保持现有 fail-closed、无自动迁移、无自动删除和显式 clear 恢复行为不变。

## Capabilities

### New Capabilities

- 无。

### Modified Capabilities

- `persistent-conversation-memory`：未知未标版本数据库的 degraded 诊断必须准确报告当前支持的 schema version。

## Impact

- 影响 `src/autopatch_j/core/memory/store.py` 的 schema 拒绝诊断，以及对应的 Memory store 可靠性测试。
- 不改变公共 API、SQLite schema、依赖、迁移策略或兼容边界。
