## Why

当前 pytest 会在普通回归中意外读取本机真实 LLM 配置并重复构造 OpenAI/httpx client，导致单测从毫秒级膨胀到数十秒，同时真实 LLM quality corpus 的触发、执行顺序和失败边界不够清晰。默认完整回归还包含平台相关换行断言和真实 Semgrep 进程，使 Windows 开发环境出现稳定失败或额外等待。

## What Changes

- 建立聚焦测试、默认确定性完整回归和真实 LLM 集成测试三层执行契约。
- 默认 `pytest -q` 禁止普通测试创建真实 OpenAI client，并继续使用真实本地 SQLite/文件系统。
- 新增 pytest 参数 `--run-live-llm`；只有显式提供后，才在全部确定性测试通过且现有 LLM 配置有效时执行完整 Memory live corpus。
- **BREAKING**：移除测试开关 `AUTOPATCH_RUN_MEMORY_LIVE_EVAL`，不保留兼容入口，也不新增替代环境变量。
- 清理 CLI/Agent/LLM 协议测试中的重复 runtime、真实 SDK client 和未关闭资源，并以 fake scanner 替代本意只验证编排持久化的真实 Semgrep 调用。
- 修复测试 fixture 的 Windows CRLF 与 POSIX permission mode 假设，使默认完整回归跨平台稳定。
- 在 `AGENTS.md` 协作规则中说明默认测试范围、真实 LLM 集成测试的明确同意要求及验证报告状态。

## Capabilities

### New Capabilities

- `layered-test-execution`: 定义默认确定性回归、真实 LLM 集成测试 gate、外部依赖隔离、失败顺序和跨平台测试契约。

### Modified Capabilities

无。

## Impact

- 主要影响 `tests/conftest.py`、pytest 配置、CLI/Agent/LLM/Scanner 相关测试 fixture、Memory quality eval 和 Windows 可移植性测试。
- 更新 `AGENTS.md` 的自动化测试协作边界；不修改面向使用者的 getting-started 与 Memory 设计文档。
- 不修改生产 LLM 请求、代理/TLS 行为、数据库 schema、CLI 产品命令或运行时环境变量。
