## Context

参见 `proposal.md`。当前 schema 读取逻辑已经通过 `MEMORY_SCHEMA_VERSION` 判断合法版本，但未知未标版本数据库的拒绝文案仍包含历史字面量 `v3`。该异常会被 `MemoryManager` 保存并投影到用户可见的 degraded status。

## Goals / Non-Goals

**Goals:**

- 让 schema 拒绝诊断与运行时支持版本保持单一事实源。
- 在用户可见的 status 边界验证版本与未知对象信息。

**Non-Goals:**

- 不改变 schema v4、bootstrap、clear、迁移或兼容策略。
- 不重命名历史测试文件，也不整理其他 Memory 诊断文案。

## Decisions

### 诊断版本引用现有 schema 常量

构造拒绝错误时使用已有的 `MEMORY_SCHEMA_VERSION`，不新增配置或辅助函数。这样版本判断和提示文本由同一常量驱动，未来 schema 升级不会再次留下过期字面量。

备选方案是直接把 `v3` 改为 `v4`；该方案当前可用，但仍会在下一次 schema 升级时重复产生同类缺陷，因此不采用。

### 通过 manager status 验证用户可见诊断

聚焦测试沿用未知未标版本数据库场景，通过 `MemoryManager.status().last_error` 断言当前版本和对象身份。相比只断言底层异常，该路径同时覆盖异常捕获和 degraded 状态投影。

## Risks / Trade-offs

- [风险] 测试若只断言完整中文句子会对无关措辞调整过于敏感。→ 分别断言动态版本片段和对象身份，不锁定整句。
- [风险] 修改拒绝逻辑可能意外触发数据库写入。→ 保留并继续断言初始化前后的数据库字节完全一致。

## Migration Plan

无需迁移。该改动只影响新产生的诊断文本；回滚时恢复原字符串即可，不影响已有数据库或导出数据。
