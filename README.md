# AutoPatch-J

<p align="center">
  <strong>把 Java 静态扫描结果变成可以审查、可以验证的补丁</strong>
</p>

AutoPatch-J 是一个面向 Java 仓库的 AI 修复 Agent，可在开发者本地通过交互式 CLI 使用，也可以接入 CI/CD 流水线。它先用 Semgrep 找出问题，再让 Agent 围绕具体 finding 读取源码并准备补丁。补丁经过人工或策略审批后写入文件，随后重新扫描原问题。

核心问题是：这份补丁能不能安全地进入开发流程。为此，finding、源码位置、补丁和验证结果之间都有明确关联。模型分析代码，程序维护状态并执行约束。

<p align="center">
  <a href="docs/getting_started.md">开始使用</a> ·
  <a href="openspec/specs/patch-safety/spec.md">Patch Safety 契约</a> ·
  <a href="docs/memory_design.md">Memory 设计</a>
</p>

![AutoPatch-J CLI 审计与补丁确认](docs/assets/autopatch-j-cli-review.png)

## 扫描器报出问题之后

静态扫描器能告诉开发者哪条规则在什么位置触发，但离一份可以合并的补丁还有几步：读取上下文、判断问题、准备修改、审核 diff，再确认原问题确实消失。直接把告警交给 LLM，还需要处理几个具体问题：

- 补丁对应的是哪个 finding？同一文件里可能有多个相似问题。
- Agent 读取源码后，文件是否又被修改过？
- 前一个补丁改变了文件长度，后面的待审补丁还能否准确定位？
- 重新扫描时，如何确认消失的是原问题，而不是别处的同规则 finding？

AutoPatch-J 把这些步骤放在同一个工作流里。扫描结果和源码证据跟着补丁一起进入审核队列，开发者不需要在扫描平台、聊天窗口和编辑器之间来回对照。

```text
Java 文件或目录
  → Semgrep 扫描快照
  → finding 身份与源码位置
  → 候选补丁
  → 人工或策略审批
  → 原子写入
  → 定向重扫
```

## 补丁如何绑定到原问题

### Finding 必须能稳定定位

每个成功归一化的 finding 都会记录规则、仓库相对路径、精确行列、byte region 和应用侧 fingerprint。相同源码重复扫描时，fingerprint 保持一致；同一文件里的重复结果也能按位置区分。Semgrep 返回的数据缺字段、带错误或位置越界时，本次扫描直接失败，不会显示成“未发现问题”。

### 补丁必须命中对应源码

Agent 提交的是 search-replace 补丁。`old_string` 必须在当前文件中唯一匹配，得到的 `match_region` 还要和目标 finding 的 region 相交。补丁修订沿用当前 finding 和扫描快照；新的 `match_region` 对不上原位置时，修订不会进入审核队列。

### 补丁按队列逐个审核

每个候选补丁都是独立的 review item，用户可以选择 `apply`、`discard` 或 `abort`。同一文件的前一个补丁造成位置平移后，系统会根据实际修改范围更新后续非重叠补丁。无法继续确认位置的草案会标记为 `STALE_DRAFT`，不能再应用或修订。

### 应用后回查原 finding

写入时只替换 `match_region` 对应的 bytes，区域外的原始 bytes 不变，并保留文件的 encoding、newline 和 permission mode。补丁应用后，原 finding 的位置会映射到修改后的文件，再由 Semgrep 重新检查。结果分为 `RESOLVED`、`STILL_PRESENT` 和 `UNVERIFIED`；文件其他位置的同规则 finding 会单独报告。

完整规则见 [Patch Safety 规格](openspec/specs/patch-safety/spec.md)。

## Harness Engineering：Agent 负责分析，程序负责流程

AutoPatch-J 的 Agent 运行在一套明确的 Harness 中。检查范围、扫描结果和审核进度由程序维护，Agent 每次只拿到当前任务需要的信息和工具：

- `Workflow` 保存扫描记录、finding 队列、补丁审核队列和应用进度。
- `Agent` 读取当前 finding 的上下文，解释问题并提出尽量小的修改。
- Agent 能调用哪些 Function Call、能读取哪些文件，由当前任务类型和检查范围决定。
- 普通代码解释和工程问答可以使用项目级 Memory；代码审计、补丁解释和补丁修订使用空历史，不读取普通对话 Memory。

对应的设计原则是 `Workflow owns state, Agent owns reasoning`。Memory 的保存内容、检索方式和隐私限制见 [Agent Memory v2 设计说明](docs/memory_design.md)。

## 审查者最终拿到什么

拿到一条扫描结果后，开发者通常要重新打开源码、确认上下文、写修改、整理理由，再回到扫描器验证结果。AutoPatch-J 把目标位置、源码证据、diff、修改理由、审核状态和重扫结论放在同一条待审记录中。

Code review 仍然是必要步骤，风险判断也仍由团队完成。工具省掉的是审核前重复定位和整理材料的工作，让审查者直接围绕源码和 diff 做决定。

## 接入 CI/CD

AutoPatch-J 可以作为 CI/CD 中的代码审计和修复步骤。扫描、finding 管理、补丁应用、审核状态和验证由独立的核心服务处理；交互式 CLI 和流水线适配层可以复用同一套处理逻辑。

一条 CI/CD 流程可以按下面的方式组织：

```text
Pull Request / Commit
  → 确定变更范围
  → 执行静态扫描
  → 生成 finding、候选 diff 和相关证据
  → 人工或策略审批
  → 应用补丁并重新扫描
  → 回写验证结果
```

流水线可以保存 finding、候选 diff 和源码证据，交给人工或团队策略审批，再应用补丁并回写重新扫描的结果。具体审批方式由团队决定，AutoPatch-J 负责提供前后一致的扫描、补丁和验证信息。

## 当前支持范围

- 面向 Java 仓库，默认使用 Semgrep。
- PMD、SpotBugs 和 Checkstyle 目前只作为规划中的扫描器展示。
- 支持本地交互式审核，也可以接入 CI/CD 的人工或策略审批流程。
- 候选补丁不会跳过审批：本地由用户确认，流水线按团队策略决定。

## 开始使用

安装、环境变量、第一次审计、补丁审核、完整命令和排障方法见 [上手与操作手册](docs/getting_started.md)。

进一步阅读：

- [Patch Safety 行为契约](openspec/specs/patch-safety/spec.md)
- [Agent Memory v2 设计说明](docs/memory_design.md)
