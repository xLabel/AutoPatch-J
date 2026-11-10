# AutoPatch-J 使用手册

## 1. 环境配置

需要 Python 3.14+，以及安装依赖和扫描器所需的网络连接。

AutoPatch-J 支持 OpenAI 兼容的模型服务，默认使用 DeepSeek Flash。使用默认模型时，只需填写 API Key；也可以配置服务地址和模型名称，接入其他模型。

| 环境变量 | 默认值 | 何时设置 |
|---|---|---|
| `AUTOPATCH_LLM_API_KEY` | 无 | 必填 |
| `AUTOPATCH_LLM_BASE_URL` | `https://api.deepseek.com` | 更换模型服务时 |
| `AUTOPATCH_LLM_MODEL` | `deepseek-flash` | 更换模型时 |

**接入其他模型**：模型服务需支持 Chat Completions 工具调用。没有内置容量配置的模型，还需同时设置以下两个变量，单位为 token：

- `AUTOPATCH_LLM_CONTEXT_WINDOW`：模型的上下文窗口大小。
- `AUTOPATCH_LLM_MAX_OUTPUT_TOKENS`：单次请求的最大输出长度。

上下文窗口须大于输出上限加 32,768 tokens，为输入和协议开销留出空间。默认 Flash 无需设置这两个变量。

**可选设置**：

- `AUTOPATCH_LLM_REQUEST_LIMIT`：单次 Agent 运行的模型请求上限，默认 `50`；填写正整数，或用 `none` 取消上限。
- `AUTOPATCH_DEBUG`：设为 `true` 开启调试输出，默认 `false`。

环境变量须在启动终端中生效。程序不自动读取 `.env`，修改配置后需重新启动。

## 2. 启动

获取代码并进入仓库根目录：

```bash
git clone https://github.com/xLabel/AutoPatch-J.git
cd AutoPatch-J
```

在同一终端中，按系统选择以下命令：

**Windows（PowerShell）**

```powershell
$env:AUTOPATCH_LLM_API_KEY="your-api-key"
.\run_on_windows.bat
```

**macOS**

```bash
export AUTOPATCH_LLM_API_KEY="your-api-key"
bash ./run_on_macos.sh
```

脚本会创建虚拟环境、安装项目依赖，并进入内置示例仓库 `examples/demo-repo`。CLI 启动后输入：

```text
/init
```

初始化会准备 Semgrep 扫描器并建立 Java 符号索引。

## 3. 修复示例

在 CLI 中输入：

```text
@src/main/java/demo/LegacyConfig.java 扫描这个文件，修复发现的问题。
```

出现文件修改范围申请后，核对文件并输入：

```text
allow
```

`allow` 只批准修改范围，不写入源码。Agent 随后展示补丁 diff 和修改理由；可以继续输入调整要求，与 Agent 讨论和修订。确认方案后输入：

```text
apply
```

应用后查看写入和验证结果。验证包括 Java 语法检查，以及已绑定告警的目标区域复扫，不自动执行编译或项目测试。

不接受当前方案时输入 `discard`；使用结束后输入 `/quit` 退出。
