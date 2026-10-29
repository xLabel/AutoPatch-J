# Python 编码规范

AutoPatch-J 的日常 Python 开发、修改、审查和重构遵循 [Google Python Style Guide 原文](reference/google/python/pyguide.md)。本页维护阅读入口、项目执行方式与明确例外，不另写一份删减版或译文。需求与设计取舍见[工程指南](ENGINEERING.md)，产品行为见 [SPEC](SPEC.md)。

## 1. 依据与适用方式

采用 Google 官方发布的规范，保留每条规则的条件、例外及建议强度。原文的“建议”“考虑”不提升为无条件限制，工具的实现能力也不能反过来定义规范。用户已确认的产品行为与授权边界仍按 [AGENTS](../AGENTS.md) 执行；发现冲突时调查依据，不为消除告警改变业务语义。

| 项目 | 固定依据 |
|---|---|
| 上游 | [google/styleguide](https://github.com/google/styleguide/tree/572c673d9058242c049fbc8248b17029726c7afa) |
| 提交 | `572c673d9058242c049fbc8248b17029726c7afa` |
| 获取日期 | 2026-10-28 |
| 官方配置 | [根 .pylintrc](../.pylintrc)，原文件名为 `pylintrc`，内容不改写 |
| 配置参数 | 排版、类型检查和开发依赖在 [pyproject.toml](../pyproject.toml)；Pylint 补充参数见本页命令 |

原件、许可和逐字节校验值见[来源说明](reference/google/python/NOTICE.md)。第三方原文及配置不承接本项目规则改写；升级须审查上游差异、许可、引用与工具适配，并更新固定提交和哈希，不在检查时自动下载最新版。

本项目执行约定：

- 保持 Python 3.14+。原文较新语法、工具或 Google 内部构建示例按本项目能力采用等价方式，不据此提高运行版本或增加内部工具依赖。
- 自有注释和 docstring 用中文清楚表达，技术标识保留英文；按适用条件使用 `Args`、`Returns`、`Yields`、`Raises`、`Attributes`。不机械补充签名已充分表达的重复说明。
- 明确豁免原文 [§3.8.2](reference/google/python/pyguide.md#s3.8.2-comments-in-modules) 对自有源码版权和许可头的要求；本约定不为项目选择许可证。第三方许可及已有声明须保留。
- function tool 的对外名称遵守产品契约中的 `FunctionToolName`，与 Python 函数或变量命名分别判断，不能为统一命名改变工具 schema。

## 2. 按任务阅读原文

下表是定位入口，不能代替原文，也不表示其余章节不适用；遇到相关语言特性时继续查对应章节。

| 本次涉及 | 阅读入口 | 在本项目重点核对 |
|---|---|---|
| 工具检查、抑制 | [§2.1 Lint](reference/google/python/pyguide.md#s2.1-lint) | 告警依据、局部抑制原因，不能通过全局关闭掩盖问题 |
| 导入与模块边界 | [§2.2](reference/google/python/pyguide.md#s2.2-imports)、[§2.3](reference/google/python/pyguide.md#s2.3-packages)、[§3.13](reference/google/python/pyguide.md#s3.13-imports-formatting) | 导入对象、完整路径、分组顺序与原文例外 |
| 异常、可变状态、默认参数 | [§2.4](reference/google/python/pyguide.md#s2.4-exceptions)、[§2.5](reference/google/python/pyguide.md#s2.5-global-state)、[§2.12](reference/google/python/pyguide.md#s2.12-default-argument-values) | 保留真实失败和状态所有权，不共享意外可变默认值 |
| 文件、连接及后台任务 | [§3.11](reference/google/python/pyguide.md#s3.11-files-sockets-closeables)、[§2.18](reference/google/python/pyguide.md#s2.18-threading) | SQLite、LLM client、文件与 worker 的创建、移交和关闭责任 |
| 命名、注释与 docstring | [§3.16](reference/google/python/pyguide.md#s3.16-naming)、[§3.8](reference/google/python/pyguide.md#s3.8-comments-and-docstrings) | 名称在调用处的语义、必要副作用和使用条件，不复述代码 |
| 排版、字符串与日志 | [§3.2](reference/google/python/pyguide.md#s3.2-line-length)、[§3.10](reference/google/python/pyguide.md#s3.10-strings)、[§3.10.1](reference/google/python/pyguide.md#s3.10.1-logging) | Black 结果之外的行长例外、字符串构造和日志调用 |
| 入口、函数组织 | [§3.17](reference/google/python/pyguide.md#s3.17-main)、[§3.18](reference/google/python/pyguide.md#s3.18-function-length) | 导入时副作用、可测试职责；约 40 行是考虑拆分的提示，不是硬上限 |
| 类型与公开接口 | [§2.21](reference/google/python/pyguide.md#s2.21-typed-code)、[§3.19](reference/google/python/pyguide.md#s3.19-type-annotations) | 新增或修改公开 API 的标注与类型检查，以及协议、外部 JSON 和已有抑制的边界 |

docstring 按公开 API、非平凡规模或非显然逻辑等原文条件判断；同时核对测试模块、重写方法等适用例外，不要求为每个私有函数机械补文档或注解。类型标注不能代替外部输入的运行时校验。

## 3. 安装与检查

在仓库根目录、已激活的项目虚拟环境中执行：

```bash
python -m pip install -c constraints.txt -e '.[test,dev]'
```

`dev` 固定 Black `26.10.1`、Pylint `4.1.3`、Pyrefly `1.4.0`；它们仅用于开发，不参与 CLI 启动。Black 和 Pyrefly 的配置同时约束工具版本。升级须同步依赖、配置与本页，并检查诊断和排版变化。

三项只读检查分别运行，保留各自退出码；一项失败后仍应取得其他项的结果：

```bash
python -m black --check src tests
```

```bash
python -m pylint --rcfile=.pylintrc --py-version=3.14 --source-roots=src,tests --recursive=y --jobs=1 --enable=wrong-import-order,consider-using-with,consider-using-join --reports=no --score=no src tests
```

```bash
python -m pyrefly check --min-severity=warn
```

检查对象为 `src` 与 `tests` 两个目录中的自有 Python 文件；新增根入口、维护脚本或源码目录时同步上述命令及类型检查输入范围。第三方原件、虚拟环境、缓存与生成物不作为自有代码检查对象。缓存使用已忽略目录，原始报告保留在会话或系统临时目录，不提交诊断台账。

Black 使用稳定默认排版、80 列和 Python 3.14 目标，不启用 preview 或 fast。原文明确允许的长导入、URL、路径和格式化器无法拆分等行长例外仍成立，docstring 首行限制另按原文核对。后续已获授权的编码任务可对本次处理的文件执行 `python -m black <文件路径>`；不得为检查顺便批量格式化无关文件。

选择 Black 是采用 Google 列举的成熟排版选项；Pyink 的附加排版选择及对既有 Google 代码风格的适配不是本项目当前需要，不并用两个格式化器。排版器负责排版，不负责翻译规范或证明业务正确。

Pylint 原样加载官方 best-effort 配置。它包含上游关闭的规则，关闭不表示对应工程责任消失，也不推测未公开的关闭理由。本项目只额外启用以下三项，命令参数属于项目取舍：

| 补充项 | 依据与收益 | 覆盖限制 |
|---|---|---|
| `wrong-import-order` | §3.13；发现标准库、第三方和项目导入的分组顺序问题 | 不证明全部字母排序、导入对象和路径正确，误分类需核对源码根及依赖 |
| `consider-using-with` | §3.11；提示可使用上下文管理的资源操作 | 返回资源、跨调用所有权和长生命周期须单独判断，不能提前关闭仍在使用的连接或 client |
| `consider-using-join` | §3.10；发现循环累计字符串的可替代写法 | 需核对输入、顺序、内存及实际行为，不为消除提示机械替换 |

暂不补开 `missing-function-docstring`、成员与导入推断检查或数量阈值，不整体开启 `R` 类：这些检查可能丢失原文条件、与类型检查重叠或增加无依据的限制。未来启用其他项，先核对原文、正反例、实际诊断、收益和误报成本；不能仅因上游关着就永久放弃，也不能仅因“更严格”就开启。

Pyrefly 采用显式 `default` preset、Python 3.14 目标、`src` 与仓库根的模块搜索路径，检查未注解函数体，命令展示警告；不采用未配置时的较弱回退，也不开启全部 strict 检查。Google 指南包含 Pyrefly 示例，它由 Meta 维护，不是 Google 唯一指定的类型检查器；工具选型与规范依据分别维护，替换时比较覆盖、诊断和抑制语义，不改写规范。

不使用 baseline、批量忽略或 `exit-zero` 获得通过。必要抑制按原文定向说明理由；已有抑制须纳入结果解释，不能把其屏蔽的内容说成检查通过。

## 4. 编码与审查

1. **编码前**：明确需求和验收，沿本次调用链定位，阅读相关原文及条件、例外；代码现状不自动成为未来设计要求。
2. **编码中**：在正确职责落实语言与工程约束，保持资源所有权和失败语义；不借排版、注解、重命名或告警处理改变业务行为，不为未来假想需求扩建机制。
3. **自检时**：运行适用的三项检查与行为验证，再核对工具未覆盖的语义。重点审查导入边界、异常捕获、可变状态、资源移交和并发收尾、命名与 docstring、公开类型契约及函数职责；测试预期仍从需求独立确定。
4. **交付时**：分别报告工具版本与配置是否有效、本次改动的问题、存量诊断、已有抑制、失败、跳过和未验证项。工具通过不证明全部规则或业务正确；Python 3.14 配置目标也不等于其他 Python 版本的运行验证。

存量不符合项按具体任务核对依据与影响，不冻结旧写法，不为追求告警归零顺手全仓整改。独立复核仍按 [AI_REVIEW](AI_REVIEW.md) 分阶段取得选择，作为主助手自检的补充。
