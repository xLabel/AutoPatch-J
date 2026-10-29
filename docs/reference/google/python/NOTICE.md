# Google Python 规范来源与许可

规范及配置来自 Google LLC 发布的 [google/styleguide](https://github.com/google/styleguide/tree/572c673d9058242c049fbc8248b17029726c7afa)，固定提交为 `572c673d9058242c049fbc8248b17029726c7afa`，获取日期为 2026-10-28。

本目录的 `pyguide.md`、`google_python_style.vim`、`LICENSE` 以及仓库根 `.pylintrc` 均按上游原始字节保存；`.pylintrc` 在上游名为 `pylintrc`。Vim 文件仅保持原文相对引用完整，不安装或修改编辑器。

根 `.gitattributes` 仅对五份第三方原件关闭 Git 换行转换，以保持检出后的字节与下表哈希一致。

依据[上游许可说明](https://github.com/google/styleguide/blob/572c673d9058242c049fbc8248b17029726c7afa/README.md#licensing)，规范文字采用 [CC BY 3.0](LICENSE)，代码示例采用 [Apache 2.0](LICENSE-APACHE-2.0.txt)。Pylint 与 Vim 配置各自保留 Google 的 Apache 2.0 声明。Apache 许可全文取自 [Apache 官方文本](https://www.apache.org/licenses/LICENSE-2.0.txt)。

本页与中文执行入口为 AutoPatch-J 自行编写的来源说明和工程约定；未改写或翻译 Google 原文。这里的第三方许可不用于声明整个 AutoPatch-J 项目的许可证。

此固定版本原文中 `#from-future-imports` 没有对应锚点，可直接阅读 [§2.20](pyguide.md#s2.20-modern-python)。保留上游原件，不为修复该链接修改正文。

## 原件与 SHA-256

| 本地文件 | 上游文件 | SHA-256 |
|---|---|---|
| [pyguide.md](pyguide.md) | [pyguide.md](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/pyguide.md) | `7ac0ba7803a5118a6c68c2aef8d8767a1d5a0026e9b3cea813796959a76ee896` |
| [google_python_style.vim](google_python_style.vim) | [google_python_style.vim](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/google_python_style.vim) | `4d5e9aecf164aaf1c8425c858112eae76ff38e1d2fc7a631ea84db635b0ed0d8` |
| [LICENSE](LICENSE) | [LICENSE](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/LICENSE) | `e6bc9e9c474700b708f568bac9e5a8a9bcb2b1dad53442f5ba449fcb848b8e76` |
| [根 .pylintrc](../../../../.pylintrc) | [pylintrc](https://raw.githubusercontent.com/google/styleguide/572c673d9058242c049fbc8248b17029726c7afa/pylintrc) | `6e7c72c556651e961fa3e393c231970acdeb73d9894c9587c5e0bc4a8c46b643` |
| [LICENSE-APACHE-2.0.txt](LICENSE-APACHE-2.0.txt) | [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0.txt) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` |

更新时审查上游差异、许可和引用，保持原件不变，同步本表与[项目执行入口](../../../PYTHON_STYLE.md)。
