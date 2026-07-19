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
