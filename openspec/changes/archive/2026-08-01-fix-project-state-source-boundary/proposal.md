## Why

当前源码读取末端已经拒绝 `.autopatch-j/**`，但默认全项目枚举仍会把解析后指向状态目录的 Java 软链接加入 focus，Semgrep target 选择又会将其规范化为状态目录路径。该缺口使主规格声明的源码范围边界没有在索引、工具和 scanner 链路中完整生效。

## What Changes

- 在项目 scope、SymbolIndex、`search_symbols`、Semgrep target 与 result parser 各自的信任边界复用现有安全路径判断，拒绝解析后落入 `.autopatch-j/**` 或仓库外的路径。
- 当 scanner result 回报状态目录路径时，在读取源码字节前让整次扫描 fail closed，不保存部分 artifact。
- 保留正常仓库源码和指向正常仓库源码的内部软链接行为，不增加路径兼容层、迁移或新配置。
- 清理三个主规格的 Purpose：补齐源码安全范围描述、删除 `context-window-management` 的 `TBD`，并将 `llm-prompt-structure` Purpose 改为中文。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `safe-source-path-resolution`：将状态目录排除契约明确覆盖默认全项目枚举、SymbolIndex/`search_symbols`、scanner targets 与 scanner results。

## Impact

- 影响 `core/project` 的 scope 与符号索引、`tools/function_calls/search_symbols.py`，以及 `scanners/semgrep` 的 target/result 路径校验。
- 用户可见变化是状态目录及其软链接不再出现在源码候选或扫描范围；异常 scanner 状态路径会报告安全扫描错误。
- 不修改公共 API、数据 schema、配置键或补丁 review 流程。
