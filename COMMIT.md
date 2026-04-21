## Commit message rules

This project uses Conventional Commits with Chinese descriptions.

Commit messages should follow this format:

```text
<type>: <中文描述>
```

Default style:

```text
英文 type + 中文描述
```

Examples:

```text
feat: 新增 init 命令
fix: 修复配置文件缺失时的启动错误
docs: 补充快速开始说明
test: 覆盖会话恢复逻辑
refactor: 简化 token 读取流程
chore: 更新开发依赖
ci: 新增 release workflow
build: 调整 npm 打包配置
```

### Allowed types

Use one of the following commit types:

```text
feat      用户可见的新功能
fix       用户可见的问题修复
docs      文档变更
test      测试相关变更
refactor  重构，不改变用户可见行为
perf      性能优化
style     代码格式、空格、lint，不改变逻辑
build     构建系统、依赖、打包配置
ci        CI/CD 配置
chore     维护性杂项
revert    回滚提交
```

### Scope rule

Do not use commit scopes in this project.

Good:

```text
feat: 新增 login 命令
fix: 修复空输入解析错误
docs: 补充安装说明
test: 覆盖无效参数场景
```

Avoid:

```text
feat(cli): 新增 login 命令
fix(config): 修复空输入解析错误
docs(readme): 补充安装说明
test(parser): 覆盖无效参数场景
```

### Subject rules

The subject should describe what this commit does.

Prefer concise Chinese verbs:

```text
新增
修复
更新
移除
重构
简化
优化
支持
处理
防止
重命名
补充
覆盖
调整
```

Good:

```text
feat: 新增 resume 命令
fix: 修复空配置导致的启动失败
test: 覆盖无效参数场景
docs: 补充本地开发说明
```

Bad:

```text
feat: 添加1个cli命令
fix: bug修好了
test: 测试一下
chore: 改东西
update code
fixed bug
misc changes
```

### Mixed Chinese and English spacing

When a commit message mixes Chinese with English words, CLI command names, file names, package names, or numbers, add spaces between Chinese and non-Chinese text.

Good:

```text
feat: 新增 login 命令
fix: 修复 config.toml 为空时的启动错误
docs: 补充 Node.js 22 安装说明
test: 覆盖 3 个会话筛选场景
chore: 升级 TypeScript 到 5.8
```

Bad:

```text
feat: 新增login命令
fix: 修复config.toml为空时的启动错误
docs: 补充Node.js 22安装说明
test: 覆盖3个会话筛选场景
chore: 升级TypeScript到5.8
```

### Language rules

Use Chinese descriptions by default because this project mainly targets Chinese-speaking users.

English descriptions are acceptable when the commit affects public APIs, npm package metadata, English documentation, or international users.

Examples:

```text
feat: add init command
docs: add English quick start guide
```

### Body rules

For simple commits, one title line is enough.

Add a body when the change needs context, especially for:

- behavior changes
- bug root causes
- trade-offs
- compatibility concerns
- migration notes
- non-obvious implementation choices

The body should explain why, not repeat the diff.

Example:

```text
fix: 修复空配置导致的启动失败

当 config.toml 存在但内容为空时，启动流程会继续读取账号信息，
最终在 TUI bootstrap 阶段报错。现在会在读取配置前校验空文件，
并回退到默认配置。
```

### Issue references

Use footers to reference issues when applicable.

Examples:

```text
fix: 修复空配置导致的启动失败

Fixes #12
```

```text
docs: 补充安装说明

Refs #8
```

### Breaking changes

For breaking changes, use `!` after the type.

Example:

```text
feat!: 调整会话列表返回结构
```

Alternatively, add a `BREAKING CHANGE:` footer.

Example:

```text
feat: 调整会话列表返回结构

BREAKING CHANGE: session.list() now returns an object with items and total instead of an array.
```

Breaking changes must clearly explain the migration impact.

### Commit granularity

Each commit should represent one logical change.

Good:

```text
feat: 新增 session 删除命令
test: 覆盖 session 删除确认流程
docs: 补充 session 管理说明
```

Avoid mixing unrelated work:

```text
feat: 新增删除命令、修改 README、升级依赖、重构配置读取
```

### AI-assisted coding rule

Do not create artificial commit histories to make AI-generated work look manually written.

It is acceptable to rewrite noisy local commits into clear logical commits before publishing.

Good:

```text
chore: 初始化项目结构
feat: 新增 session 列表命令
feat: 新增 session 删除命令
test: 覆盖会话筛选逻辑
docs: 补充快速开始说明
```

Bad:

```text
update
more changes
fix
final
final2
human work
```

### Before committing

Before creating a commit, Codex should:

1. inspect the diff;
2. ensure the commit contains one logical change;
3. choose the correct type;
4. write a concise subject;
5. add a body only when useful;
6. never commit secrets, tokens, generated junk, or unrelated formatting changes;
7. never add a scope to the commit message.

### Recommended response when preparing commits

When Codex prepares or suggests commits, respond with:

```text
建议提交：
- feat: 新增 init 命令
- test: 覆盖 init 参数校验
- docs: 补充 CLI 使用示例

说明：
- 每个 commit 对应一个逻辑变更
- 未使用 scope
- 未包含无关格式化改动
- 未发现敏感信息
```