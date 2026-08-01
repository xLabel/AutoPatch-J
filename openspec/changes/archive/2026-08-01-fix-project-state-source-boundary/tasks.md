## 1. 规格对齐

- [x] 1.1 更新三个主规格 Purpose，并将本 change 的 `safe-source-path-resolution` delta 同步到主规格。
- [x] 1.2 运行 `openspec validate fix-project-state-source-boundary --strict`，确认 proposal、design、spec 与 tasks 一致。

## 2. 源码范围纵深防线

- [x] 2.1 修复默认项目 scope 与 SymbolIndex 枚举，排除解析后位于状态目录或仓库外的路径，同时保留安全内部软链接行为。
- [x] 2.2 让 `search_symbols` 在所有 focus 状态下过滤不安全路径，阻止旧索引记录进入 LLM 工具结果。
- [x] 2.3 在 Semgrep target 与 result 路径入口增加状态目录校验，并确保非法 result 在读取源码前使整次扫描 fail closed。

## 3. 回归测试

- [x] 3.1 增加 project scope、SymbolIndex 与 `search_symbols` 对状态目录软链接和安全内部软链接的聚焦测试。
- [x] 3.2 增加 Semgrep target/result 对直接状态路径、软链接状态路径和无正文错误的聚焦测试。

## 4. 验证

- [x] 4.1 运行相关源码范围、索引、工具和 Semgrep 聚焦测试。
- [x] 4.2 运行 `pytest -q` 确定性完整回归、`openspec validate --all --strict` 和 change verify；真实 LLM 集成测试保持未运行。
