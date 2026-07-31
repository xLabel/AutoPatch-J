## 1. OpenSpec 与契约对齐

- [x] 1.1 将 Memory v3 delta specs 同步到主规格，并建立 `stabilize-memory-llm-contract` change。
- [x] 1.2 严格校验主规格、父 change 与当前 change，并在父 change 中完成拆分交接记录。

## 2. Memory LLM 请求与输出契约

- [x] 2.1 为 Memory primary/repair 请求实现标准 JSON 模式、标准 dialect、禁用 provider 扩展及 SDK 隐式重试，同时保持非 Memory 请求行为不变。
- [x] 2.2 收紧 extraction/consolidation prompt、JSON 解析和语义校验，输出安全的结构化失败诊断。
- [x] 2.3 实现一次完整 JSON repair、repair 前 lease 续期、容量预检与每次模型调用预算保留。

## 3. 有界任务状态与运行时可见性

- [x] 3.1 升级 Memory schema v4，加入 `failed`、`llm_call_count`、最多两次 job attempt、最多三次模型调用、同质 extraction claim 和有界 lease 回收。
- [x] 3.2 将终态失败、失败计数和安全诊断贯穿 Memory manager、状态模型、CLI 展示及导出语义。

## 4. 测试、文档与验证

- [x] 4.1 增加请求、输出契约、pipeline、store、lease、CLI 和安全诊断的聚焦确定性测试。
- [x] 4.2 更新确定性质量评估替身，并增加两个最小真实 LLM smoke tests；本 change 未获单独授权时不得运行真实 LLM 测试。
- [x] 4.3 创建 provider 清理交接文档，明确遗留适配范围、建议的独立 change 和无兼容迁移决策；本 change 不删除全局 provider 适配。
- [x] 4.4 运行聚焦测试、`pytest -q`、`git diff --check` 和 OpenSpec 严格校验，并明确记录真实 LLM 集成测试“未运行”。

## 5. 真实 LLM 验证发现后的修复

- [x] 5.1 在获得明确授权后运行完整 `pytest -q --run-live-llm`；首错为 extraction repair 在 1800-token 输出上限处以 `finish_reason=length` 截断，严格 parser 正确拒绝不完整 JSON。
- [x] 5.2 将 extraction primary/repair 输出预算调整为 4096，并增加请求策略回归断言。
- [x] 5.3 重复完整 live suite；此前 extraction 首错已消失，新首错为 consolidation repair 在 2200-token 输出上限处以 `finish_reason=length` 截断。
- [x] 5.4 将 consolidation primary/repair 输出预算调整为 4096，并增加请求策略回归断言。
- [x] 5.5 重复完整 live suite；两个长度截断首错均已消失，新首错为 consolidation 的 payload-relative 语义偏差绕过 repair，最终由 Store 抛出 internal `MemoryContractError`。
- [x] 5.6 补齐 consolidation 纯语义 validator 与聚焦回归，使可由当前 payload 判断的 Store 合同偏差触发既定一次 repair。
- [x] 5.7 单点 live 复验此前失败的 `assistant-only-claim-is-not-decision` case 并通过；重复完整 live suite 后无 job failure，新首错为质量评估器只在补充 `content` 中匹配 active 内容，忽略 canonical `statement` 中已正确生成的 revision 2 决定。
- [x] 5.8 将质量 corpus 的 active 内容断言与 Memory 字段职责对齐，同时匹配 `statement` 与 `content`。
- [x] 5.9 单点 live 复验 `decision-supersedes-old-decision` 并通过；重复完整 live suite 后前 17 个 cases 均通过，最后一个 `english-undecided-is-not-decision` 暴露模型从单纯 “not decided” 陈述推断了后续延续意图。
- [x] 5.10 在 extraction contract 中增加中英文负例，禁止从未决定状态自行推断 future discussion，并增加 prompt 回归断言。
- [x] 5.11 运行确定性完整回归和 OpenSpec 严格校验；第六次完整 `pytest -q --run-live-llm` 实际完成全部 18 个 live cases，最终结果为 `498 passed`。

## 6. 归档前规格重对齐

- [x] 6.1 补齐 `typed-memory-runtime` 对 schema v4 的既有运行时场景修改，消除与 `persistent-conversation-memory` 的版本冲突。
- [x] 6.2 将四个 delta specs 智能合并到主规格，并逐项复核新增和修改内容。
- [x] 6.3 运行 OpenSpec 严格校验，并确认主规格与归档输入无冲突。
