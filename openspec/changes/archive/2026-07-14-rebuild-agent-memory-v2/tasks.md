## 1. 规格与基线

- [x] 1.1 创建 proposal、design 和 delta specs，锁定 SQLite、RAW、无向量、thread/repair、命令与删除边界。
- [x] 1.2 运行 `openspec validate rebuild-agent-memory-v2 --strict`，修复所有规格校验问题。

## 2. SQLite 与 typed Memory core

- [x] 2.1 实现 SQLite v2 schema、typed records、连接/事务配置、active thread 约束和 v1 `memory.json` 直接删除。
- [x] 2.2 实现 turn 生命周期、thread compaction projection、typed status/list/show/forget/clear/export 与显式存储错误。
- [x] 2.3 实现 durable job、lease、generation fencing、retry backoff、startup recovery 和一次性 flush/close。
- [x] 2.4 增加存储、并发、损坏、clear fencing、thread 恢复和 legacy 删除聚焦测试并运行通过。

## 3. 两阶段处理与无向量检索

- [x] 3.1 实现严格 JSON extraction contract、来源 quote 校验、短确认双来源规则和 rolling thread compaction。
- [x] 3.2 实现 consolidation operations、append-only revision、supersede/reject/forget suppression 与事务回滚。
- [x] 3.3 实现 routing context、规范化 retrieval terms、确定性 search/read 和 usage 更新。
- [x] 3.4 增加 extraction、consolidation、retry、中文/英文检索、无命中和遗忘回归测试并运行通过。

## 4. Agent 与工具集成

- [x] 4.1 将 ReAct/Presenter 改为请求级 typed result，移除共享 `_messages`，并让 ordinary 保存 RAW user 与最终可见 assistant turn。
- [x] 4.2 将源码读取 cache 改为请求级，确保所有 repair 请求使用空历史且不读写 Memory。
- [x] 4.3 新增 `memory_search`、`memory_read` function tools、ordinary task profile 和 bounded Memory Context prompt。
- [x] 4.4 增加 ordinary 跨重启连续、repair 隔离、工具白名单、prompt 预算和 trace 不落库测试并运行通过。

## 5. CLI 与 runtime 生命周期

- [x] 5.1 扩展命令解析、help 和 completion，实现 `/new` 与 `/memory status|list|show|forget|clear|export`。
- [x] 5.2 实现 `/new` abort+thread 切换、`/reset` 保留 Memory、RAW export、启动恢复和退出一次性 job 处理。
- [x] 5.3 增加命令参数、删除边界、pending patch、reset 保留、export、退出失败恢复和幂等 close 测试并运行通过。

## 6. LLM 策略、质量与文档

- [x] 6.1 将 `MEMORY_SUMMARY` 拆为 extraction/consolidation purpose，接入独立 background client、token budget、timeout 和诊断。
- [x] 6.2 新增版本化 Memory quality corpus、deterministic fake-LLM eval 与显式开关的可选 live eval。
- [x] 6.3 删除 v1 JSON/delta/normalizer/trigger/repo-profile Memory 代码与过时测试，重写 Memory 设计文档和 README。

## 7. 完整验证与交付

- [x] 7.1 运行全部聚焦测试与 `.venv/bin/pytest -q`，修复所有回归。
- [x] 7.2 再次运行 OpenSpec strict validation，并用 `openspec-verify-change` 核对任务、需求、场景和实现。
- [x] 7.3 检查 `git status --short` 与 diff，确认未触碰既有 `.codex/skills/*` 用户改动，并停在未 commit 的 review 状态。

## 8. 实现审查后的生产硬化

- [x] 8.1 更新 `design.md`，明确 turn owner lease、快照式 flush、固定查询数检索、确定性证据 gate 和安全诊断边界。
- [x] 8.2 实现并发 owner fencing、worker 异常隔离、结构错误批次失败、语义弱候选过滤和索引优先的 routing budget。
- [x] 8.3 增加并发、快照 flush、证据 gate、SQL 查询预算、诊断脱敏与 routing budget 聚焦测试并运行通过。

## 9. 验证发现后的修复

- [x] 9.1 更新 `design.md`，补充 consolidation snapshot claim、超长检索输入和 provider diagnostic 的实现边界。
- [x] 9.2 将 consolidation allowlist/thread 条件前移到 SQL `LIMIT` 前，限制规范化 search query，并移除任意 provider code 诊断。
- [x] 9.3 增加 consolidation stale completion/failure、双 manager claim、并发 flush、forget suppression、超长 query 和敏感诊断测试。
- [x] 9.4 增加 legacy 删除失败、真实 `/new` routing、clear 保留 export、repair 不落库和 classifier fallback debug 回归测试。

## 10. 验收反馈后的诊断边界重对齐

> 本阶段的 10.1、10.2 与 10.4 显式替代 8.1、8.3、9.2、9.3 中关于 provider-safe、诊断脱敏和移除 provider code 的旧边界；既有完成状态保留为执行历史。

- [x] 10.1 更新 proposal、design、delta specs 和 Memory 文档，明确 debug-only RAW provider 诊断、20,000 字符持久化边界及独立 context budget change。
- [x] 10.2 实现统一 RAW LLM exception/body formatter，并接入 client、intent classifier、conversation router 与 Memory pipeline。
- [x] 10.3 实现 Memory job/status 的有界 RAW 错误持久化，以及 `/memory status` 普通提示与 debug RAW 展示。
- [x] 10.4 补充 RAW 诊断、自然 extraction 调度、consolidation 原子回滚和 inactive Memory 直接读取测试。
- [x] 10.5 运行聚焦测试、全量 pytest、compileall、diff check、OpenSpec strict validation 和 verify-change。

## 11. 严格验收后的状态与渲染修复

- [x] 11.1 更新 proposal、design、delta specs 和 Memory 文档，明确 unresolved error、literal RAW rendering 和既定 budget 边界。
- [x] 11.2 修复 unresolved job 与 `memory_meta.last_error` 的同步语义。
- [x] 11.3 将动态错误改为 literal Rich rendering。
- [x] 11.4 增加状态重算、lease recovery 和 markup-like RAW 回归测试。
- [x] 11.5 运行聚焦测试、全量 pytest、compileall、diff check、OpenSpec strict validation 和 verify-change。

## 12. 再次严格验收后的确定性与并发修复

- [x] 12.1 更新 `design.md`、delta spec 与本执行账本，明确全局错误统一重算和并发 RAW export 契约。
- [x] 12.2 修复同一 `updated_at` 下 `record_job_failure()` 绕过 `id DESC` tie-break 的问题，并增加状态重算回归测试。
- [x] 12.3 为 RAW export 实现 exclusive filename reservation、唯一临时文件和清理路径，并增加固定时钟并发回归测试。
- [x] 12.4 增加 extraction/consolidation 严格 JSON contract 的直接负例测试，不改动已符合契约的 parser。
- [x] 12.5 运行聚焦测试、全量 pytest、compileall、diff check 和 OpenSpec strict validation。

## 13. 最终 coherence 验收修复

- [x] 13.1 将 `/memory clear` 的全局错误清理对齐为删除 jobs 后调用统一重算，并复用既有 clear 回归断言。
- [x] 13.2 重新运行聚焦测试、全量 pytest、compileall、diff check、OpenSpec strict validation 和最终实现验收。

## 14. 未知 SQLite 接管边界修复

- [x] 14.1 更新 `design.md`、delta spec、Memory 文档和本执行账本，明确未版本化数据库的 fail-closed identity 边界。
- [x] 14.2 增加未知非空表、未知空 schema、空白库、并发初始化和显式 clear 恢复测试，并确认旧实现能够复现偏差。
- [x] 14.3 实现启用 WAL 前的数据库 identity preflight、事务内重校验和确定性 schema object 拒绝逻辑。
- [x] 14.4 运行聚焦测试、全量 pytest、compileall、diff check、OpenSpec strict validation 和最终实现验收。

## 15. v2 schema 与 bootstrap fail-closed 修复

- [x] 15.1 更新 `design.md`、delta specs、Memory 文档和本执行账本，明确 v2 validate-only、精确 catalog ABI 与 active thread create/require 边界。
- [x] 15.2 增加缺表、缺约束、缺 index、多余 object、缺 bootstrap state、运行时 typed failure 和显式 clear 恢复测试，并确认旧实现偏差。
- [x] 15.3 实现非 WAL 初始化状态机、规范 schema manifest、v2 只读验证和验证成功后启用 WAL。
- [x] 15.4 分离 active thread create/require，统一 meta/active typed invariant 与 degraded status。
- [x] 15.5 运行聚焦测试、全量 pytest、compileall、diff check、OpenSpec strict validation 和最终严格验收。

## 16. WARNING 规格重对齐与运行时修复

- [x] 16.1 更新 `design.md`、`typed-memory-runtime`、`persistent-conversation-memory` 与本执行账本，明确选择性 operational guard、诊断/恢复边界、请求级 thread 绑定及三段 SQL 预算。
- [x] 16.2 实现统一 bootstrap guard、默认事务校验、选择性 operational connection 与 recovery clear。
- [x] 16.3 将 ordinary workflow、history、routing、`memory_search` 和 `memory_read` 绑定到 `begin_turn()` 返回的请求 thread。
- [x] 16.4 增加 bootstrap WARNING、显式恢复、并发 `/new`、绑定清理与完整 SQL trace 回归测试。
- [x] 16.5 运行聚焦测试、deterministic quality corpus、全量 pytest、compileall、diff check、OpenSpec strict validation 和零 WARNING 最终验收。
