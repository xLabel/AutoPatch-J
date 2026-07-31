## 1. pytest 分层与真实 LLM gate

- [x] 1.1 新增 `live_llm` marker、`--run-live-llm`、live 后置排序、基础失败阻断、配置预检和终端摘要，并增加聚焦策略测试。
- [x] 1.2 将完整 Memory live corpus 迁移到 marker 和 session-scoped 真实 client，移除 `AUTOPATCH_RUN_MEMORY_LIVE_EVAL`。

## 2. 默认测试外部依赖隔离

- [x] 2.1 为非 live 测试增加真实 OpenAI client fail-closed guard，并让 LLM 协议及 Agent helper 在构造前使用显式 mock/fake。
- [x] 2.2 清理 CLI 测试的重复 runtime 初始化、真实 LLM factory 和资源 teardown。
- [x] 2.3 让 scanner runner 持久化测试使用 fake `ScannerCatalog`，保留 mock subprocess adapter 覆盖。

## 3. Windows 可移植性

- [x] 3.1 将按 LF 计算 offset/fingerprint 的 fixture 改为确定的 UTF-8 bytes，并运行 patch、finding 和 scanner identity 聚焦测试。
- [x] 3.2 让 GBK 编码跨平台验证、POSIX permission 按平台断言，并保留现有 SQLite clear connection 修复。

## 4. 协作规则与文档

- [x] 4.1 在 `AGENTS.md` 记录默认测试与真实 LLM 集成测试的明确同意边界。
- [x] 4.2 更新 getting-started 与 Memory 设计文档，统一 pytest 命令、术语和旧开关移除说明。（后续由 6.4 替代：提交评审决定恢复两个已有文档。）

## 5. 验证

- [x] 5.1 运行测试策略、LLM、Agent、CLI、Scanner、Windows 和 Memory clear 聚焦回归，检查慢测试列表。
- [x] 5.2 运行 `pytest -q --durations=20`、`git diff --check` 和 `openspec validate establish-layered-test-suite --strict`。
- [x] 5.3 默认回归全部通过后，说明 16 cases、19 turns 和约 19–38 次真实请求；取得用户明确同意后运行 `pytest -q --run-live-llm`。（多次授权运行已验证确定性测试先行、配置预检、共享 client 与首错 gate；真实 case 的业务契约失败已转交 active Memory change，不再阻塞分层测试 change 归档。）

## 6. 归档前收口

- [x] 6.1 根据多次授权运行结果重对齐 design 与 delta spec：完整 corpus 指收集 16 个 case，执行仍受首错 gate 约束；case/turn/call 范围由执行同意流程说明。
- [x] 6.2 修复真实 LLM 配置摘要的 URL userinfo 泄漏，并增加 credentials、path、query 与 fragment 的聚焦脱敏测试。
- [x] 6.3 记录 Memory live 验证发现的系统性问题，运行默认完整回归、差异检查与严格校验，随后同步主规格并归档。
- [x] 6.4 根据提交评审恢复 `docs/getting_started.md` 与 `docs/memory_design.md`，测试授权边界只保留在 `AGENTS.md`。
