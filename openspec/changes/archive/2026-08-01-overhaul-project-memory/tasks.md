## 1. Context capacity foundation

- [x] 1.1 Add DeepSeek 1M model profile, environment overrides, output reserve validation, conservative token estimator, and focused config/request tests.
- [x] 1.2 Add typed request context budgets, preflight pressure calculation, and deterministic component accounting tests.
- [x] 1.3 Implement replayable tool-result pruning, structured runtime checkpoint, recent-tail rebuild, compaction progress guard, and single overflow retry with ReAct tests.

## 2. Clean Memory v3 domain

- [x] 2.1 Replace v2 records/contracts with v3 subject, statement, strength, origin, recall mode, applicability, revision and RecallQuery/RecallPolicy/Map types.
- [x] 2.2 Replace the SQLite catalog and repository projections with clean v3 initialization, strict non-v3 rejection, Store-owned logical identity and current-revision provenance.
- [x] 2.3 Update extraction/consolidation prompts and validation for explicit/adopted/inferred signals, local runtime constraints, cross-kind identity and apply/procedure rejection.

## 3. Recall and Agent integration

- [x] 3.1 Implement deterministic RecallQuery term normalization, path/kind eligibility, standing/relevant lanes, lexical abstention and token-bounded Map/search/read.
- [x] 3.2 Add request-local RecallPolicy, readable-ID admission, search/read call budgets and ordinary-versus-repair repository enforcement.
- [x] 3.3 Move Memory to synthetic advisory user context, expose restricted Memory tools to repair profiles, and rebuild context across ReAct calls without persisting synthetic messages.

## 4. Thread lifecycle and recovery

- [x] 4.1 Replace `/new` single flush with old-thread watermark processing, bounded timeout warning, persistent retry continuation and thread-binding cleanup.
- [x] 4.2 Preserve Memory fail-closed behavior while allowing core scan/source/patch workflows to continue with a visible unavailable warning.

## 5. Verification

- [x] 5.1 Replace v2 Memory tests and fixtures with v3 store, contract, pipeline, recall, isolation, conflict, thread and quality corpus coverage.
- [x] 5.2 Run focused context/LLM/Agent/Memory/CLI tests, then run `pytest -q`, `git diff --check`, and `openspec validate overhaul-project-memory --strict`; fix every regression.

## 6. Memory design document

- [x] 6.1 Rewrite only `docs/memory_design.md` from the verified implementation, emphasizing runtime memory, compaction, 1M context, recall/injection, intent isolation and concrete trade-offs without OpenSpec links or planning language.
- [x] 6.2 Verify document source references, relative links, terminology, implementation constants and `rg -ni 'openspec|openspec/' docs/memory_design.md`.

## 7. 验证中发现后的实现校准

- [x] 7.1 拆分 thread checkpoint 与 durable Memory Map 的注入和 token 账本，并让 hard rebuild 缩减 Map target。
- [x] 7.2 为 repair turn 增加 finding evidence key 与 bounded recent repair evidence，验证同 finding 重试不会被误学成长期偏好。
- [x] 7.3 增加 degraded 只读 show/export recovery view、主流程降级续行，以及 `/new` 五秒 watermark 等待边界。
- [x] 7.4 重新运行完整测试、OpenSpec strict validation 和文档验收。
- [x] 7.5 修复 repeated runtime compaction 越过当前 turn 后丢失原始 user message，并增加连续压缩回归测试。
- [x] 7.6 让所有 LLM purpose 的实际输出上限受 context profile reserve 封顶，保持窗口预算守恒。

## 8. 文档重构

- [x] 8.1 根据用户评审重构 `docs/memory_design.md`：以请求信息流组织内容，使用中文原生标题和行文，并在英文术语首次出现时补充中文释义。
- [x] 8.2 核对重构后的文档与当前实现一致，检查术语、源码路径、链接、Markdown 格式和 OpenSpec 严格校验。

## 9. CR 验证后的边界修复

- [x] 9.1 重对齐 proposal、design 与 delta specs，明确局部 repair、完整 discussion 语义、request-local binding、Memory 收尾降级和 compaction input capacity 契约。
- [x] 9.2 收紧 durable candidate 校验，拒绝局部 repair 与拆字段代码事实，同时保留合法长期决定和 inferred repetition。
- [x] 9.3 修复 `/new` 对已 admission 请求的状态清理，并扩展 turn lease/not-found 等 Memory operational error 的降级边界。
- [x] 9.4 按实际 input capacity 重做 compaction fragment 预算，并补齐 getting-started 的 context 配置说明。
- [x] 9.5 增加聚焦回归测试，运行完整 pytest、OpenSpec strict validation 与 `git diff --check`。

## 10. 第二轮 CR 修复

- [x] 10.1 更新 design、delta specs 与任务账本，明确 degraded history、中文词项、finding evidence identity、当前 batch provenance 和 watermark heartbeat。
- [x] 10.2 实现五项聚焦修复，不增加 schema、依赖、迁移或新后台线程。
- [x] 10.3 增加真实运行组合的聚焦回归测试，并同步 Memory 设计文档。
- [x] 10.4 运行聚焦测试、完整 pytest、两份 OpenSpec strict validation 与 `git diff --check`。

## 11. 第三轮 CR 修复

- [x] 11.1 更新 design、delta specs 与任务账本，明确当前输入排除、local hard rebuild、checkpoint 降级、来源级 evidence admission 与 turn scope 上限。
- [x] 11.2 实现五项聚焦修复，不增加 schema、依赖、迁移、配置项或新后台抽象。
- [x] 11.3 增加对应回归测试，并同步 Memory 设计文档。
- [x] 11.4 运行聚焦测试、完整 pytest、两份 OpenSpec strict validation 与 `git diff --check`。

## 12. 第四轮 CR 修复

- [x] 12.1 更新 design、delta specs 与任务账本，明确所有 purpose preflight、超长 RAW 投影、review item constraint、审计完成语义、watermark lifecycle 与运行时 schema recovery。
- [x] 12.2 实现 LLM 容量、compaction retry、review constraint、turn 收尾与 code-audit 结果修复。
- [x] 12.3 实现 watermark subset/等待/线程生命周期、extraction provenance、consolidation identity 与 runtime schema/clear 修复。
- [x] 12.4 增加聚焦回归测试，运行完整 pytest、OpenSpec strict validation 与 `git diff --check`。

## 13. 第五轮 CR 修复

- [x] 13.1 更新 design、delta specs 与任务账本，明确 consolidation registry、capacity candidate 终态、实际 extraction provenance、checkpoint 标记预算、显式 search 词项和 summary runtime guard。
- [x] 13.2 实现六项聚焦修复，不增加 schema、依赖、迁移、配置项或新后台抽象。
- [x] 13.3 增加对应回归测试，并同步 Memory 设计文档。
- [x] 13.4 运行聚焦测试、完整 pytest、OpenSpec strict validation 与 `git diff --check`。

## 14. 第六轮 CR 修复

- [x] 14.1 更新 design、delta specs 与任务账本，明确 patch revision 完成边界、`memory_read` 错误分类和 watermark 异步测试语义。
- [x] 14.2 在 workflow 最终结果产生后完成 `patch_revise` turn，并显式区分 `memory_read` 的 not-found、storage degraded 与 contract error。
- [x] 14.3 增加 patch revision 持久化、Memory 读取错误分类回归测试，并将 watermark 启动断言改为 bounded eventual wait。
- [x] 14.4 运行聚焦测试、watermark 重复测试、完整 pytest、OpenSpec strict validation 与 `git diff --check`。

## 15. 第七轮 CR 修复

- [x] 15.1 更新 design、delta specs 与任务账本，明确 close 后 watermark 不得启动、revise identity 冲突拒绝和 classifier 容量 fallback。
- [x] 15.2 修复 watermark thread 的停止检查、登记/启动竞态，以及 Memory revise 的 active identity 冲突校验。
- [x] 15.3 将 classifier 固定 envelope 超容量转换为既有 `REVIEW_CONTINUE` fallback，并增加三项聚焦回归测试。
- [x] 15.4 运行聚焦测试、完整 pytest、OpenSpec strict validation 与 `git diff --check`。

## 16. 第八轮 CR 修复

- [x] 16.1 更新 design、delta specs 与任务账本，明确 input-capacity 缩放的 compaction 门槛、完整 active identity、runtime schema stale、实际 Map/search 账本与 terminal skip 提示。
- [x] 16.2 实现七项聚焦修复，不增加 schema、依赖、迁移、配置项或新后台线程。
- [x] 16.3 增加小窗口、完整 identity、worker stale、Map/search wire 计费、search 错误分类与 terminal skip 回归测试，并同步 Memory 设计文档。
- [x] 16.4 运行聚焦测试、完整 pytest、OpenSpec strict validation 与 `git diff --check`。

## 17. 真实 LLM 验证发现后的跨语言召回修复

- [x] 17.1 重对齐 proposal、design、delta spec 与任务账本，明确中文项目决定的稳定英文语义 alias 和阻断性质量门槛。
- [x] 17.2 强化 extraction/consolidation prompt，并同步 Memory 设计文档；不增加生产二次 LLM 调用或新检索组件。（文档同步后续由 22.3 替代：提交评审决定恢复已有 Memory 设计文档。）
- [x] 17.3 增加 prompt contract 回归测试，保留 `backward compatibility` corpus 硬断言，并运行默认完整回归。
- [x] 17.4 在默认回归通过后取得明确同意，运行完整真实 LLM 集成测试并完成两份 change 的严格校验。（先行确定性回归发现 Windows 并发 export 竞态，最终由 18.4 替代完成；18.4 通过后同时关闭。）

## 18. 真实 LLM 验证先行回归发现后的 Windows export 修复

- [x] 18.1 记录 Windows 同时间戳并发 RAW export 的文件发布竞态与最小互斥方案；既有行为规格保持不变。
- [x] 18.2 修复同进程多 manager/thread 的文件发布竞态，并增加 `PermissionError` 冲突、真实权限错误和失败清理回归测试。
- [x] 18.3 运行聚焦测试、30 次并发压力测试、默认完整回归、`git diff --check` 与两份 change 的严格校验。
- [x] 18.4 确定性层全部通过后重新取得明确同意，运行完整真实 LLM 集成测试；通过后同时关闭 17.4 与分层测试 5.3。（第二次 live run 的确定性层和前五个 live case 通过，第六个 discussion case 失败；最终由 19.4 替代完成。）

## 19. 真实 LLM 验证发现后的 user-backed discussion 修复

- [x] 19.1 重对齐 delta spec、design 与任务账本，区分 user 支持的未决项目讨论和 assistant-only preference/decision。
- [x] 19.2 澄清 extraction prompt 并增加 prompt contract 回归测试；不修改 corpus、parser、Store 或生产调用次数。
- [x] 19.3 运行 Memory quality 聚焦测试、默认完整回归、`git diff --check` 与两份 change 的严格校验。
- [x] 19.4 确定性层全部通过后重新取得明确同意，运行完整真实 LLM 集成测试；通过后同时关闭 17.4、18.4 与分层测试 5.3。（第三次 live run 仍在同一含糊 discussion fixture 失败；最终由 20.4 替代完成。）

## 20. 真实 LLM 重复失败后的 test-contract 校准

- [x] 20.1 审核业务规格、真实模型结果与中英文 corpus，确认含糊的一次性意见询问不应强制产生跨 turn discussion。
- [x] 20.2 将 assistant-only case 改为显式“暂时不决定、后续继续评估”，保留 discussion 正向期望并同时禁止 preference/decision。
- [x] 20.3 运行 Memory quality 聚焦测试、默认完整回归、`git diff --check` 与两份 change 的严格校验。
- [x] 20.4 确定性层全部通过后重新取得明确同意，运行完整真实 LLM 集成测试；通过后同时关闭 17.4、18.4、19.4 与分层测试 5.3。（20.4 授权运行的前七个 live case 通过，第八个 thread discussion fixture 因缺少明确延续信号失败；最终由 21.4 替代完成。）

## 21. 真实 LLM 验证发现后的 thread discussion fixture 校准

- [x] 21.1 审核完整 16-case corpus、业务规格、确定性 fake 与 20.4 真实结果，记录只有第八个 fixture 不满足现行 discussion 前置条件；不修改 proposal、delta spec 或生产行为。
- [x] 21.2 将 thread discussion fixture 改为 user 明确“暂时不决定、后续继续评估”且由 user 提供 `retry backoff` 语义，并增加独立 fixture-contract 回归测试。
- [x] 21.3 运行 Memory quality 聚焦测试、默认完整回归、`git diff --check` 与两份 change 的严格校验。
- [x] 21.4 确定性层全部通过后重新取得明确同意，运行完整真实 LLM 集成测试；通过后同时关闭 17.4、18.4、19.4、20.4。（已授权运行中第 1 个 live case 通过，第 2 个 case 因 `thread_compaction` 未满足非空字符串契约而失败，后续 14 个 case 按首错 gate 跳过；系统性修复转交 22.2。）

## 22. 系统性修复交接

- [x] 22.1 在本 change 的 design 与任务账本中记录多次真实 LLM 集成验证发现、已修问题和最新契约失败；本阶段不修改 Memory 业务实现。
- [x] 22.2 在后续独立 thread 建立新的 OpenSpec change，建议命名为 `stabilize-memory-llm-contract`；先统一 prompt、parser、pipeline、Store、诊断和重试契约，再取得明确同意恢复真实 LLM 集成测试。（已建立并严格校验 `stabilize-memory-llm-contract`；真实 LLM 集成测试仍等待单独明确授权。）
- [x] 22.3 根据提交评审恢复 `docs/memory_design.md`，不在产品设计文档中加入本轮测试执行或质量校准说明；规格与任务账本保留行为契约和审计历史。

## 23. 独立合同修复完成后的最终验证

- [x] 23.1 在用户明确授权下，由 `stabilize-memory-llm-contract` 完成系统性修复并重复真实验证；最终完整 `pytest -q --run-live-llm` 实际完成全部 18 个 live cases，结果为 `498 passed`。
- [x] 23.2 运行默认完整确定性回归、主规格与两份 change 的严格校验以及 `git diff --check`，并据最终 live 结果关闭 17.4、18.4、19.4、20.4 与 21.4。

## 24. 最终审计发现的 watermark deadline 提示竞态

- [x] 24.1 修复 worker 在 deadline 临界点以 `pending>0` 完成时遗漏“任务已保留并在后台继续”提示的竞态，不改变等待上限或任务状态。
- [x] 24.2 连续 30 次运行 deadline 聚焦测试并全部通过；Memory pipeline 聚焦测试 `20 passed`，默认完整回归 `480 passed, 18 skipped`，主规格与两份 change 严格校验通过，`git diff --check` 无错误。
