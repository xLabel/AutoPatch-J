# layered-test-execution Specification

## Purpose

为 AutoPatch-J 建立快速、确定、跨平台的默认回归，并以显式、可审计的方式在基础测试通过后运行真实 LLM 集成测试。

## Requirements

### Requirement: 默认完整回归不得访问真实 LLM
系统 SHALL 将 `pytest -q` 定义为确定性完整回归；真实 LLM 配置存在时，普通测试仍不得创建真实 OpenAI client 或发起真实 LLM 请求。

#### Scenario: 开发环境已配置真实 LLM
- **WHEN** 开发者在已设置 `AUTOPATCH_LLM_*` 的环境执行 `pytest -q`
- **THEN** 所有非 live 测试使用 fake 或 mock LLM 依赖
- **AND** 真实 LLM 集成测试以明确状态跳过

#### Scenario: 普通测试意外创建真实 client
- **WHEN** 非 live 测试尝试创建真实 OpenAI client
- **THEN** 测试立即失败并说明应注入 fake 或 mock
- **AND** 不得以静默替换掩盖错误的测试依赖边界

### Requirement: 真实 LLM 集成测试必须显式触发且后置执行
系统 SHALL 只在 pytest 收到 `--run-live-llm` 时执行标记为 `live_llm` 的测试，并 SHALL 将这些测试排在全部确定性测试之后。

#### Scenario: 未提供真实 LLM 参数
- **WHEN** pytest 未收到 `--run-live-llm`
- **THEN** 所有 `live_llm` 测试被跳过
- **AND** 测试摘要说明真实 LLM 集成测试未运行

#### Scenario: 基础测试存在失败
- **WHEN** pytest 收到 `--run-live-llm`，但任一先行确定性测试失败或发生错误
- **THEN** 所有尚未执行的真实 LLM 集成测试被跳过
- **AND** 不发起真实 LLM 请求

#### Scenario: 真实 LLM 配置缺失或无效
- **WHEN** 确定性测试全部通过且 pytest 收到 `--run-live-llm`，但现有 LLM 配置未就绪或无效
- **THEN** 真实 LLM 集成层以非零结果失败
- **AND** 配置检查在首个真实请求之前完成

#### Scenario: 真实 LLM 配置摘要脱敏
- **WHEN** 现有 LLM 配置有效且真实 LLM 集成层准备创建共享 client
- **THEN** 测试摘要可以显示 model 与 endpoint origin
- **AND** 摘要不得显示 URL userinfo、path、query、fragment 或 API key

#### Scenario: 首个 live case 失败
- **WHEN** 任一真实 LLM corpus case 失败
- **THEN** 后续 live case 全部跳过
- **AND** 测试摘要区分失败 case 与未执行 case

### Requirement: Memory live corpus 使用受控的真实 client 生命周期
系统 SHALL 在真实 LLM 集成层收集完整版本化 Memory quality corpus，按顺序执行尚未被首错 gate 阻断的 case，并 SHALL 在同一 pytest session 内复用一个真实 LLM client。

#### Scenario: 完整 Memory corpus 执行
- **WHEN** 基础测试通过、现有 LLM 配置有效且收到 `--run-live-llm`
- **THEN** 系统收集全部 16 个 Memory quality case 并按顺序执行
- **AND** 任一 case 失败后的剩余 case 按首错 gate 跳过；无失败时全部 16 个 case 均执行
- **AND** 每个 case 使用独立临时数据库但共享同一个真实 LLM client
- **AND** session 结束时关闭底层 SDK client

### Requirement: 默认回归必须跨平台且不依赖已安装 scanner
系统 SHALL 让用于字节偏移的测试 fixture 使用确定的换行 bytes，并 SHALL 只在支持 POSIX mode 的平台断言 POSIX permission；仅验证扫描编排持久化的测试不得启动真实 Semgrep。

#### Scenario: Windows 执行默认回归
- **WHEN** 在 Windows 运行 `pytest -q`
- **THEN** LF fixture 的 offset、finding region 与 fingerprint 断言不受 CRLF 自动转换影响
- **AND** GBK 编码行为仍被验证
- **AND** POSIX permission 断言不会产生错误失败

#### Scenario: 未安装 Semgrep runtime
- **WHEN** 默认完整回归运行在未安装 managed Semgrep 的环境
- **THEN** scanner runner 持久化测试使用确定性 fake scanner
- **AND** Semgrep adapter 的命令与结果处理由 mock subprocess 测试验证
