# AutoPatch-J

AutoPatch-J 是一款专为 Java 项目设计的极简 AI 代码补丁智能体。它采用 **ReAct (Reasoning and Acting)** 模式，能够自主扫描代码漏洞、理解上下文并生成高质量的极简修复补丁。

## 核心特性

- **ReAct 智能决策**: 能够自主思考、调用工具、观察结果并进行自我修正。
- **三级质量门禁**:
  1. **物理校验**: 确保补丁的“查找-替换”逻辑在目标文件中唯一匹配。
  2. **语法校验**: 基于 Tree-sitter 确保生成的 Java 代码符合语法规范。
  3. **语义验证**: 自动触发静默重扫，验证漏洞是否真正被消除。
- **极致交互体验**:
  - 输入 `@` 符号触发 IDE 级的类、方法和路径实时补全。
  - 漂亮的 Rich 终端渲染，包含 Diff 高亮和浮动动作面板。
  - 非阻塞门禁，支持在补丁审核期间持续对话反馈。
- **混合式 RAG**: 基于符号索引和句柄管理，支持长对话记忆且极度节省 Token。
- **工程化底座**: 统一配置中心，全局资源与项目状态严格隔离。

## 交互效果展示

当你请求 Agent 修复漏洞时，你会看到类似下方的精美终端反馈：

```text
╭────────────────── AutoPatch-J: AI-Powered Java Security Expert ──────────────────╮
│ 输入 /help 查看命令，使用 @ 符号绑定上下文。                                     │
╰──────────────────────────────────────────────────────────────────────────────────╯
当前项目: E:\AutoPatch-J\examples\demo-repo

[Agent 思考中...]
... (Agent 正在生成极简修复补丁) ...

 预览: src/main/java/demo/UserService.java ──────────────────────────────────────────
  @@ -15,4 +15,5 @@
   public void login(String username, String password) {
-      if (password.equals("123456")) {
+      if ("123456".equals(password)) {
           // 登录逻辑
   }
 ───────────────────────────────────────────────────────────────────────────────────

╭────────────────────────────── 补丁待审核 (PENDING) ──────────────────────────────╮
│ 文件: src/main/java/demo/UserService.java                                        │
│ 统计: +1行 -1行  校验: Java 语法校验通过                                         │
│                                                                                  │
│ 意图: 将易触发空指针的 equals 调用改为常量在前的安全写法。                       │
│ ────────────────────────────────────────                                         │
│ apply   🚀 应用此补丁并执行三级语义校验                                          │
│ discard 🗑️ 丢弃此草案并清理缓冲区                                                │
│ <文本>  💬 直接输入反馈让 Agent 重新生成                                         │
╰──────────────────────────────────────────────────────────────────────────────────╯

[PENDING] autopatch-j> apply

➜ 正在应用补丁至 src/main/java/demo/UserService.java...
✔ 补丁物理应用成功！
➜ 正在执行语义验证（重新扫描）...
✔ 语义校验通过：规则 [java.correctness.nullable-equals] 已在该位置消失。

autopatch-j> _
```

## 项目架构 (V2.1)

```text
src/autopatch_j/
├── cli/              # 交互表现层 (主程序、自动补全、Rich 渲染)
├── core/             # 核心服务 (持久化、代码抓取、索引、补丁引擎、验证)
├── tools/            # 模型工具适配器 (Scan, Edit, Search, Read, Detail)
├── scanners/         # 扫描器驱动 (Semgrep 等底层封装)
├── validators/       # 语法校验器 (基于 Tree-sitter 的 Java 校验)
├── config.py         # 全局配置中心 (环境变量、默认值统一收口)
├── paths.py          # 物理路径映射 (全局资源与项目隔离逻辑)
├── __init__.py
└── __main__.py       # CLI 程序入口
```

## 快速开始

### 1. 环境准备
设置环境变量（支持 OpenAI 兼容接口）：
```bash
export LLM_API_KEY="your-api-key"
export LLM_MODEL="gpt-4o"
```

### 2. 安装与运行
```bash
pip install -e .
autopatch-j
```

### 3. 典型交互流程
在 CLI 中输入以下指令：
```text
/init                  # 初始化环境并建立项目索引
扫描项目中的安全问题      # 触发 Agent 调用扫描工具
帮我修复 F1 处的漏洞      # 针对特定 ID 进行修复
apply                  # 在预览面板中确认并应用补丁
```

## 开发者指导

请参阅 [AGENTS.md](AGENTS.md) 了解详细的协作规则和开发约定。

## 演示仓库

我们在 `examples/demo-repo` 提供了一个可运行的示例，用于快速体验完整的扫描与修复闭环。
