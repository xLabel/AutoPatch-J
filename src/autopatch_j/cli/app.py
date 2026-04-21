from __future__ import annotations

import sys
import re
import signal
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from autopatch_j.paths import discover_repo_root, get_project_state_dir
from autopatch_j.core.artifact_manager import ArtifactManager
from autopatch_j.core.index_service import IndexService
from autopatch_j.core.patch_engine import PatchEngine
from autopatch_j.core.code_fetcher import CodeFetcher
from autopatch_j.core.service_context import ServiceContext
from autopatch_j.agent.agent import AutoPatchAgent
from autopatch_j.cli.render import CliRenderer
from autopatch_j.cli.completer import AutoPatchCompleter
from autopatch_j.config import GlobalConfig


class AutoPatchCLI:
    """
    AutoPatch-J 主控制台 (Application Layer)
    职责：整合所有核心服务，驱动人机交互循环。
    """

    def __init__(self, cwd: Path) -> None:
        self.cwd = cwd.resolve()
        self.repo_root = discover_repo_root(self.cwd)
        self.renderer = CliRenderer()
        
        # 统一上下文容器
        self.context: ServiceContext | None = None
        self.agent: AutoPatchAgent | None = None

        if self.repo_root:
            self._init_services(self.repo_root)

        # 注册信号处理器 (Ctrl+C)
        signal.signal(signal.SIGINT, self._handle_interrupt)

        # 🚀 接入升级后的全能补全器
        self.prompt_session = PromptSession(
            completer=AutoPatchCompleter(self.context.indexer.search if self.context else lambda _: []),
            history=FileHistory(str(get_project_state_dir(self.repo_root) / "history.txt")) if self.repo_root else None
        )

    def _handle_interrupt(self, signum, frame):
        """处理 Ctrl+C 信号"""
        self.renderer.print("\n[bold yellow]检测到中断信号，正在安全退出...[/bold yellow]")
        sys.exit(0)

    def _init_services(self, repo_root: Path) -> None:
        """初始化单例 Service 并注入 Context (基于显式依赖注入)"""
        # 1. 显式创建各层 Service
        artifacts = ArtifactManager(repo_root)
        # 注入 GlobalConfig 里的参数，保持 Service 的纯净和可测试性
        indexer = IndexService(repo_root, ignored_dirs=GlobalConfig.ignored_dirs)
        patch_engine = PatchEngine(repo_root)
        fetcher = CodeFetcher(repo_root)
        
        # 2. 组装 Context 容器
        self.context = ServiceContext(
            repo_root=repo_root,
            artifacts=artifacts,
            indexer=indexer,
            patch_engine=patch_engine,
            fetcher=fetcher
        )
        # 3. 将 Context 注入大脑
        self.agent = AutoPatchAgent(self.context)

    def run(self) -> int:
        self.renderer.print_panel(
            "AutoPatch-J V2.4: 极致工程化版 Java 补丁智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="欢迎使用",
            style="bold blue"
        )
        
        if not self.repo_root:
            self.renderer.print_info("未检测到 Java 项目。请进入项目目录并执行 /init。")
        else:
            self.renderer.print(f"当前项目: [bold cyan]{self.repo_root}[/bold cyan]")

        try:
            while True:
                # --- 门禁检查：是否存在待确认补丁 ---
                pending = self.context.artifacts.fetch_pending_patch() if self.context else None
                prompt_prefix = "autopatch-j"
                
                if pending:
                    self.renderer.print_diff(pending.diff, title=f" 预览: {pending.file_path} ")
                    self.renderer.print_action_panel(
                        file_path=pending.file_path,
                        diff=pending.diff,
                        validation=pending.validation_status,
                        rationale=pending.rationale or "无说明"
                    )
                    prompt_prefix = "[bold yellow]PENDING[/bold yellow] autopatch-j"

                try:
                    user_input = self.prompt_session.prompt(f"{prompt_prefix}> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break # 优雅退出循环

                if not user_input:
                    continue

                # --- 处理门禁确认关键字 ---
                if pending:
                    if user_input.lower() == "apply":
                        self.handle_apply(pending)
                        continue
                    elif user_input.lower() == "discard":
                        self.handle_discard()
                        continue

                # --- 处理指令或对话 ---
                if user_input.startswith("/"):
                    self.handle_command(user_input)
                else:
                    self.handle_chat(user_input)
            
            return 0
        finally:
            # 退出时的资源清理点
            pass

    def handle_chat(self, text: str) -> None:
        """处理自然语言对话及上下文注入"""
        if not self.agent or not self.context:
            self.renderer.print_error("Agent 未就绪。请先执行 /init。")
            return

        # 1. 上下文注入
        mentions = re.findall(r'@([\w\.]+)', text)
        extra_context = ""
        for m in mentions:
            entries = self.context.indexer.search(m, limit=1)
            if entries:
                entry = entries[0]
                code = self.context.fetcher.fetch_by_index_entry(entry)
                extra_context += f"\n--- 引用代码片段 ({entry.kind}: {entry.name}) ---\n{code}\n"
        
        final_prompt = text
        if extra_context:
            self.renderer.print_step(f"已自动注入 {len(mentions)} 处代码上下文...")
            final_prompt = f"{extra_context}\n--- 用户请求 ---\n{text}"

        self.renderer.print()
        with self.renderer.console.status("[bold yellow]Agent 正在思考修复路径...", spinner="dots") as status:
            def on_thought_token(token: str) -> None:
                self.renderer.print(token, end="", style="dim italic")

            def on_tool_start(tool_name: str) -> None:
                status.update(f"[bold blue]正在执行工具: {tool_name}...")

            self.agent.chat(
                final_prompt, 
                on_thought_token=on_thought_token, 
                on_tool_start=on_tool_start
            )
        self.renderer.print()

    def handle_command(self, raw_cmd: str) -> None:
        parts = raw_cmd.split()
        cmd = parts[0].lower()

        if cmd == "/init": self.handle_init()
        elif cmd == "/status": self.handle_status()
        elif cmd == "/reindex": self.handle_reindex()
        elif cmd == "/scanner": self.handle_scanners()
        elif cmd == "/help": self.handle_help()
        elif cmd in ("/quit", "/exit"): sys.exit(0)
        else: self.renderer.print_error(f"未知命令: {cmd}")

    def handle_help(self) -> None:
        """展示精美的指令看板"""
        from rich.table import Table
        from rich.panel import Panel

        # 1. 系统指令表
        sys_table = Table(show_header=True, header_style="bold magenta", box=None)
        sys_table.add_column("系统命令", style="cyan", width=15)
        sys_table.add_column("功能描述")
        
        sys_table.add_row("/init", "初始化当前目录为 Java 项目并建立索引")
        sys_table.add_row("/status", "查看当前项目状态、LLM 模型与索引统计")
        sys_table.add_row("/scanner", "查看扫描器蓝图与运行时状态")
        sys_table.add_row("/reindex", "强制重新扫描全项符号（适用于手动大改后）")
        sys_table.add_row("/help", "显示此指令看板")
        sys_table.add_row("/quit", "安全退出程序")

        # 2. 交互指令表
        act_table = Table(show_header=True, header_style="bold green", box=None)
        act_table.add_column("交互关键字", style="yellow", width=15)
        act_table.add_column("用法说明")
        
        act_table.add_row("@符号", "在对话中输入 @ 触发类/方法/路径的实时补全")
        act_table.add_row("apply", "在补丁预览状态下输入，执行物理修复与三级验证")
        act_table.add_row("discard", "在补丁预览状态下输入，丢弃当前草案并清空缓存")
        act_table.add_row("自然语言", "直接输入您的意图，如 '扫描安全问题' 或 '解释这段代码'")

        self.renderer.print_panel("AutoPatch-J 指令中心", style="bold blue")
        self.renderer.console.print(sys_table)
        self.renderer.print("\n[bold]💡 交互与门禁指引[/bold]")
        self.renderer.console.print(act_table)
        self.renderer.print("\n[dim]提示：输入指令时无需带参数。Agent 会根据上下文自动处理细节。[/dim]")

    def handle_scanners(self) -> None:
        """展示所有静态扫描器的状态与规划"""
        from autopatch_j.scanners import ALL_SCANNERS
        from rich.table import Table

        table = Table(title="Java 静态扫描器看板", show_header=True, header_style="bold magenta")
        table.add_column("名称", style="cyan", width=12)
        table.add_column("状态", width=25)
        table.add_column("版本", justify="center")
        table.add_column("功能简述")

        for scanner in ALL_SCANNERS:
            # 获取元数据
            meta = scanner.get_meta(self.repo_root)
            
            status_text = f"[green]● {meta.status}[/green]" if meta.is_implemented else f"[dim]○ {meta.status}[/dim]"
            version_text = meta.version if meta.is_implemented else "-"
            
            table.add_row(
                meta.name,
                status_text,
                version_text,
                meta.description
            )

        self.renderer.console.print(table)
        self.renderer.print("\n[italic]提示：目前系统默认启用 Semgrep。未来版本将支持多选扫描器进行交叉验证。[/italic]")

    def handle_init(self) -> None:
        self.renderer.print_step("正在初始化 AutoPatch-J 环境...")
        self.repo_root = self.cwd
        
        # 1. 初始化单例 Service 容器
        self._init_services(self.repo_root)

        # 2. 运行时自检
        from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime
        status, _ = install_managed_semgrep_runtime()
        self.renderer.print_step(f"扫描器运行时自检: {status}")
        
        # 3. 建立索引
        assert self.context is not None
        stats = self.context.indexer.perform_rebuild()
        self.renderer.print_success(f"初始化完成！索引项: {stats.get('total', 0)}")

    def handle_status(self) -> None:
        if not self.context: return
        stats = self.context.indexer.get_stats()
        self.renderer.print_panel(f"项目: {self.repo_root}\n索引: {stats}", title="系统状态")

    def handle_reindex(self) -> None:
        if not self.context: return
        self.renderer.print_step("正在重新构建索引...")
        stats = self.context.indexer.perform_rebuild()
        self.renderer.print_success(f"索引刷新完成 ({stats.get('total', 0)})")

    def handle_apply(self, pending: Any) -> None:
        assert self.context is not None
        self.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        if self.context.patch_engine.perform_apply(pending):
            self.renderer.print_success("物理应用成功！")
            
            # 自动化第三级验证
            from autopatch_j.core.validator_service import SemanticValidator
            from autopatch_j.scanners import get_scanner, DEFAULT_SCANNER_NAME
            scanner = get_scanner(DEFAULT_SCANNER_NAME)
            if scanner:
                validator = SemanticValidator(self.repo_root, scanner)
                success, msg = validator.perform_verification(pending)
                if success: self.renderer.print_success(msg)
                else: self.renderer.print_error(msg)
            
            self.context.artifacts.clear_pending_patch()
        else:
            self.renderer.print_error("应用失败。")

    def handle_discard(self) -> None:
        if self.context:
            self.context.artifacts.clear_pending_patch()
            self.renderer.print_info("已丢弃补丁。")

def main() -> int:
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
