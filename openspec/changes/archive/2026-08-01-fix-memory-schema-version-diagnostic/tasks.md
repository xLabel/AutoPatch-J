## 1. 诊断实现

- [x] 1.1 让未知未标版本数据库的拒绝诊断引用当前 `MEMORY_SCHEMA_VERSION`，并保持 fail-closed 行为不变。

## 2. 聚焦测试

- [x] 2.1 扩展未知 schema 可靠性测试，覆盖用户可见的当前版本、对象身份、无损拒绝和显式 clear。

## 3. 验证

- [x] 3.1 运行 Memory store 聚焦测试和确定性完整回归测试。
- [x] 3.2 运行 OpenSpec 严格校验并完成 change 的完整性、正确性和一致性验收。
