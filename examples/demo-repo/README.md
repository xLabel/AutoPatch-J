# AutoPatch-J 演示仓库

本项目用于演示 AutoPatch-J 的核心审计、补丁生成和人工确认流程。

## 演示内容

仓库里保留了几类刻意写出的 Java 问题，便于观察 Agent 如何基于静态扫描 finding 读取证据、生成补丁草案，并把补丁交给人工确认。

- `LegacyConfig.java`：包含未关闭 IO 流、`Optional.get()` 未检查、字符串比较顺序等问题。
- `UserService.java`：包含空指针和集合使用相关示例。
- `Util.java`：包含可触发基础规则的工具类示例。

## 推荐操作

在仓库根目录启动 AutoPatch-J 后，可以依次尝试：

```text
/init
@LegacyConfig.java 检查代码
为什么这么改？
apply
```

也可以使用 `/status` 查看工作台状态，或使用 `/doctor` 排查 LLM、扫描器和本地索引是否可用。
