## Context

当前测试有三条隐藏的慢路径：CLI/Agent 根据 import-time `GlobalConfig` 自动创建真实 LLM client；LLM 协议测试先创建真实 OpenAI/httpx client 再替换内部 client；scanner runner 持久化测试直接启动 managed Semgrep。Memory live corpus 则通过会残留在 shell 中的环境变量启用，16 个参数化 case 各自创建 client，也没有基础测试先行或首个失败停止边界。

现有版本化 corpus 包含 16 个 case、19 个 turn。每个 turn 产生一次 extraction，合法候选可能再产生 consolidation，因此单次完整 live eval 约有 19–38 次业务级调用。

## Goals / Non-Goals

**Goals:**

- 让默认完整回归在真实 LLM 配置存在时仍保持确定、快速和无真实外部请求。
- 用一个 pytest 参数表达真实 LLM 集成测试意图，并以程序侧 gate 保证基础测试先行。
- 保留完整 Memory live corpus，同时共享 client、首个 live 失败即停止。
- 清理重复 runtime、未关闭测试资源、真实 scanner 慢路径和 Windows fixture 假设。

**Non-Goals:**

- 不修改生产 LLM factory、OpenAI/httpx 代理或 TLS 行为。
- 不增加生产配置、兼容环境变量、CI workflow、数据库 schema 或产品 CLI 命令。
- 不为测试性能引入 client cache、延迟 transport 或新的第三方测试依赖。

## Decisions

### 1. pytest marker 与单一命令行参数

使用 `live_llm` marker 和 `--run-live-llm`。collection hook 将所有 live item 稳定移动到末尾；未提供参数时添加 skip。live item setup 检查 session 已有失败，因而基础层失败和首个 live 失败都会阻止后续真实请求。

相比新增环境变量，pytest 参数只对当前命令生效，不会残留在 shell。相比增加独立 runner 脚本，hook 保持既有 `pytest` 入口和较低心智负担。

### 2. 普通测试使用 fail-closed 的真实 SDK guard

非 live 测试通过 autouse fixture 将 `autopatch_j.llm.transport.openai.OpenAI` 替换为抛出明确断言的构造器。普通测试必须显式注入 fake；LLM 协议测试则通过专用 fixture 在构造 `LLMClient` 前覆盖为 `MagicMock`。

选择失败而不是全局返回静默 mock，是为了让未来新增的错误依赖立即暴露。live marker 不安装该 guard。

### 3. live 配置与 client 生命周期

首个 live item setup 使用当前 `GlobalConfig` 检查 readiness、extra body 和 context profile，再由现有 default factory 创建一个 session-scoped client。配置无效时在构造或请求之前使测试失败；client 在全部 live case 间顺序复用，并在 fixture teardown 关闭底层 SDK client。

不修改生产 retry 和 purpose token 上限，确保 corpus 验证真实运行配置。取得执行同意前，由协作流程说明 16 个 case、19 个 turn 和约 19–38 次业务级调用；pytest 开始 live 层时只显示 model 与脱敏 endpoint origin。endpoint 摘要不得包含 URL userinfo、path、query、fragment 或 API key。

### 4. 测试依赖只在测试侧收口

CLI fixture 在构造 `AutoPatchCli` 前 patch app 层 factory，删除重复 `initialize_runtime()`，并在 teardown 调用 `clear_runtime()`。直接构造 `Agent` 的 session/tool 测试传入 fake LLM；仅验证 model label 的测试使用带 `model` 属性的 stub。

scanner runner 测试通过现有 `ScannerCatalog` 构造 fake scanner，保持 production scanner 接口不变。

### 5. 跨平台 fixture 表达真实测试意图

测试若按 LF 字符串计算 byte offset，就以 `newline=""` 写入确定的 UTF-8 bytes；已有 CRLF/混合换行产品测试保持不变。GBK 编码断言跨平台保留，permission 断言比较应用补丁前后的平台可观察 mode，避免在 Windows 假设完整 POSIX mode 语义。

## Risks / Trade-offs

- **[pytest hook 依赖 collection 顺序]** → 对排序、skip、配置失败和首个 live 失败增加聚焦测试，并在终端摘要中报告状态。
- **[共享 live client 可能积累 diagnostics]** → corpus 不依赖完整 diagnostics 历史，调用仍顺序执行；session 结束显式关闭 client。
- **[配置摘要可能泄漏 endpoint 凭据或私有路由]** → 只显示去除 userinfo、path、query 与 fragment 的 endpoint origin，并用聚焦测试固定边界。
- **[全局非 live guard 可能暴露更多旧测试]** → 逐个改为显式 fake，禁止通过清空真实环境参数绕过问题。
- **[Windows fixture 固定 LF 可能削弱换行覆盖]** → 仅修改本来就按 LF 计算预期的 fixture，保留专门的 CRLF 与混合换行测试。

## Migration Plan

1. 新增 marker、pytest 参数和 guard，同时将现有 live corpus 改为 marker。
2. 修复所有被 guard 揭示的普通测试真实 client 构造。
3. 删除 `AUTOPATCH_RUN_MEMORY_LIVE_EVAL` 常量和读取，不保留 fallback。
4. 修复 Windows 与 scanner 测试并运行默认完整回归。
5. 默认回归通过后再向用户说明真实测试范围并取得明确同意；未同意前不执行 live corpus。

## 决策记录

- 2026-07-31：提交评审后撤回 getting-started 与 Memory 设计文档中的测试执行说明，仅在 `AGENTS.md` 保留自动化协作边界；原因是测试分层属于开发协作约束，不应扩张面向使用者的产品文档。
