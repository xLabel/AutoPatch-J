## ADDED Requirements

### Requirement: 项目状态目录不得作为 LLM 源码范围
系统 SHALL 将归一化后位于 `.autopatch-j/**` 的路径排除在 Agent focus、源码 scope 和所有 LLM source-read 结果之外。

#### Scenario: 精确状态文件路径
- **WHEN** 用户或 LLM 提供一个存在的 `.autopatch-j/**` 精确文件路径
- **THEN** scope resolver 和源码读取服务拒绝该路径
- **AND** 错误不得包含文件正文

#### Scenario: 状态目录或软链接路径
- **WHEN** 用户提供状态目录，或仓库内路径解析后落入 `.autopatch-j/**`
- **THEN** 该目录不得展开为 focus 文件
- **AND** SourceReader 与 function tools 不得返回其中内容

#### Scenario: 正常项目源码
- **WHEN** 路径安全地位于仓库中且不属于 `.autopatch-j/**`
- **THEN** 继续使用既有 focus、歧义候选和仓库边界规则读取源码
