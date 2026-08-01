## Purpose

定义源码范围与读取工具的安全路径解析契约：缺失或同名候选只能在当前 focus 内确定性解析，仓库边界、软链接和 `.autopatch-j/**` 状态目录不得进入 Agent focus、scanner 范围或 LLM source-read 结果。

## Requirements

### Requirement: 歧义源码路径不得自动纠正
当多个候选文件匹配请求文件名时，源码读取工具 SHALL NOT 静默将缺失路径替换为第一个索引文件。

#### Scenario: 存在唯一 in-focus 候选
- **WHEN** 源码读取工具收到一个缺失路径，且该文件名只有一个 in-focus 索引匹配
- **THEN** 工具可以读取该唯一候选

#### Scenario: 存在多个候选
- **WHEN** 源码读取工具收到一个缺失路径，且该文件名有多个 in-focus 索引匹配
- **THEN** 工具返回列出候选路径的错误，而不是读取任意文件

### Requirement: 项目状态目录不得作为 LLM 源码范围
系统 SHALL 将解析并归一化后位于 `.autopatch-j/**` 的路径排除在 Agent focus、源码 scope、SymbolIndex/符号搜索结果、scanner targets、scanner results 和所有 LLM source-read 结果之外。

#### Scenario: 精确状态文件路径
- **WHEN** 用户或 LLM 提供一个存在的 `.autopatch-j/**` 精确文件路径
- **THEN** scope resolver 和源码读取服务拒绝该路径
- **AND** 错误不得包含文件正文

#### Scenario: 状态目录或软链接路径
- **WHEN** 用户提供状态目录，或仓库内路径解析后落入 `.autopatch-j/**`
- **THEN** 该目录或路径不得展开为 focus 文件
- **AND** SourceReader 与 function tools 不得返回其中内容

#### Scenario: 默认项目范围包含状态目录软链接
- **WHEN** 默认全项目枚举发现一个 Java 路径，且该路径解析后落入 `.autopatch-j/**`
- **THEN** 该路径不得进入项目 focus、SymbolIndex、符号搜索结果或 scanner targets

#### Scenario: 旧索引包含状态目录记录
- **WHEN** 符号搜索读取到一个解析后落入 `.autopatch-j/**` 的旧索引或伪造记录
- **THEN** 无论当前 focus 是否锁定，function tool 都不得向 LLM 返回该记录

#### Scenario: Scanner 回报状态目录 finding
- **WHEN** scanner result 的路径解析后落入 `.autopatch-j/**`
- **THEN** 系统在读取该路径源码字节前拒绝整个扫描结果
- **AND** 扫描返回 error、不得产出部分 finding artifact，错误不得包含文件正文

#### Scenario: 正常项目源码
- **WHEN** 路径安全地位于仓库中且不属于 `.autopatch-j/**`
- **THEN** 继续使用既有 focus、歧义候选、符号索引、scanner 和仓库边界规则处理源码
- **AND** 指向正常仓库源码的内部软链接保持可用
