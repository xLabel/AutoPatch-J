# AutoPatch-J

AutoPatch-J 是一款面向 Java 安全与正确性修复的工业级补丁智能体。它通过静态扫描引擎与大语言模型的深度编排，实现漏洞的自动发现、补丁起草与三级质量门禁验证。

## 核心能力

- **显式工程架构**: 基于核心 Service 的构造函数依赖直注，严禁使用隐式容器，实现高度解耦与可测试性。
- **三级验证门禁**: 物理唯一性匹配 -> Tree-sitter 语法检查 -> 自动化语义重扫。
- **智能上下文交互**: 支持 @ 符号实时补全代码符号，实现精准 RAG 注入。
- **深度适配 DeepSeek**: 原生支持百炼思考链 (Reasoning Content) 显示。

## 快速开始

### 1. 环境准备
确保已安装 Python 3.10+，并配置 LLM 相关环境变量：
```bash
SET LLM_API_KEY=your_key_here
SET LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
SET LLM_MODEL=deepseek-v3
```

### 2. 启动程序
双击根目录下的 `run.bat` 或运行：
```bash
python -m autopatch_j
```

### 3. 基础交互
- `/init`: 初始化项目并建立符号索引。
- `/scanner`: 查看各类型扫描器的就绪状态。
- `扫描项目并修复漏洞`: 发起自然语言指令。

## 交互示例

当 Agent 生成补丁后，您将看到如下预览：

```text
文件: src/main/java/demo/Auth.java  统计: +1行 -1行  校验: ok
意图: 替换不安全的 MD5 哈希算法为 SHA-256。

────────────────────────────────────────
apply   > 应用此补丁并执行三级验证
discard > 丢弃此草案并清理缓冲区
<文本>  > 直接输入反馈让 Agent 重新生成
```

## 开发者

基于“显式工程范式”构建。欢迎通过 Pull Request 提交更多 Java 扫描器适配。
