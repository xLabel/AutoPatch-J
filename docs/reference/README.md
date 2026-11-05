# Google Python 规范来源与许可

规范及配置来自 Google LLC 发布的 [google/styleguide](https://github.com/google/styleguide/tree/572c673d9058242c049fbc8248b17029726c7afa)，固定提交为 `572c673d9058242c049fbc8248b17029726c7afa`，获取日期为 2026-10-28。三仓资料于 2026-11-03 对齐；本次复用已固定的仓库内资料，没有重新下载上游文件。

本地 `GOOGLE_PYTHON_STYLE.md` 对应上游 `pyguide.md`，仅将其中的 Vim 附件相对链接改为[同一固定提交的官方地址](https://github.com/google/styleguide/blob/572c673d9058242c049fbc8248b17029726c7afa/google_python_style.vim)。不再保存本地 Vim 附件，查看该附件需要联网；指南其余字节保持不变，规范正文仍可离线阅读。

本目录的 `GOOGLE_STYLE_LICENSE`、`APACHE_LICENSE_2.0.txt` 及仓库根 `.pylintrc` 均按各自来源的原始字节保存；`.pylintrc` 在上游名为 `pylintrc`，是本仓库唯一生效的 Pylint 配置。根 `.gitattributes` 对指南、两份许可和根 `.pylintrc` 共四份第三方参考文件关闭 Git 换行转换，保护各自已登记的本地字节与哈希。

依据[上游许可说明](https://github.com/google/styleguide/blob/572c673d9058242c049fbc8248b17029726c7afa/README.md#licensing)，规范文字采用 [CC BY 3.0](GOOGLE_STYLE_LICENSE)，代码示例采用 [Apache 2.0](APACHE_LICENSE_2.0.txt)。Pylint 配置保留 Google 的 Apache 2.0 声明。Apache 许可全文取自 [Apache 官方文本](https://www.apache.org/licenses/LICENSE-2.0.txt)。两份许可说明第三方资料的使用与分发条件，不作为日常编码必读材料。

本页与中文执行入口为 AutoPatch-J 自行编写的来源说明和工程约定；除上述附件链接适配外，未改写或翻译 Google 规范正文。这里的第三方许可不用于声明整个 AutoPatch-J 项目的许可证。

此固定版本原文中 `#from-future-imports` 没有对应锚点，可直接阅读 [§2.20](GOOGLE_PYTHON_STYLE.md#s2.20-modern-python)。不为修复该链接修改规范正文。

## 来源与 SHA-256

指南上游原始文件的 SHA-256 为 `7ac0ba7803a5118a6c68c2aef8d8767a1d5a0026e9b3cea813796959a76ee896`；下表登记适配后的本地哈希，反向恢复唯一附件链接后应与该原始哈希一致。其余三份文件未作字节适配，本地哈希与各自来源原件相同。

| 本地文件 | 上游文件 | 本地 SHA-256 |
|---|---|---|
| [GOOGLE_PYTHON_STYLE.md](GOOGLE_PYTHON_STYLE.md) | [pyguide.md](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/pyguide.md) | `af7b9b672943654b051d9c48dcfc86c46396a40fbe548134827949fc5d573ccb` |
| [GOOGLE_STYLE_LICENSE](GOOGLE_STYLE_LICENSE) | [LICENSE](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/LICENSE) | `e6bc9e9c474700b708f568bac9e5a8a9bcb2b1dad53442f5ba449fcb848b8e76` |
| [根 .pylintrc](../../.pylintrc) | [pylintrc](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/pylintrc) | `6e7c72c556651e961fa3e393c231970acdeb73d9894c9587c5e0bc4a8c46b643` |
| [APACHE_LICENSE_2.0.txt](APACHE_LICENSE_2.0.txt) | [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |

更新时审查上游差异、许可和引用，保留规范正文与必要声明；任何本地适配均须明确登记，并更新相应哈希及[项目执行入口](../PYTHON_STYLE.md)。
