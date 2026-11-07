# AutoPatch-J 演示仓库

本项目用于演示 AutoPatch-J 的核心审计、补丁生成和人工确认流程。

## 演示内容

仓库里保留了几类刻意写出的 Java 问题，便于观察 Agent 如何基于静态扫描 finding 读取证据、生成补丁草案，并把补丁交给人工确认。

- `LegacyConfig.java`：包含未关闭 IO 流、`Optional.get()` 未检查、字符串比较顺序等问题。
- `UserService.java`：包含空指针和集合使用相关示例。
- `Util.java`：包含可触发基础规则的工具类示例。

## 推荐操作

在本演示目录启动 AutoPatch-J 后，可以依次尝试：

```text
/init
@src/main/java/demo/LegacyConfig.java 检查代码，核实问题后准备一项修复
allow
为什么这么改？
apply
```

界面提出确切文件的写范围申请后才输入 `allow`；完整提案出现后可以解释或修订，确认后输入 `apply`。扫描候选未必需要修改，应以源码和业务契约判断。

使用 `/status` 查看模型配置、扫描器和索引状态，`/scanner` 查看扫描器信息。源码变化后再次执行 `/init` 刷新索引；已就绪扫描器不会重装。
