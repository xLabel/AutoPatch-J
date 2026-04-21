from __future__ import annotations

import sys
import re
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
from autopatch_j.cli.completer import MentionCompleter


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

        # 初始化交互会话
        history_path = get_project_state_dir(self.repo_root) / "history.txt" if self.repo_root else None
        self.prompt_session = PromptSession(
            completer=MentionCompleter(self.context.indexer.search if self.context else lambda _: []),
            history=FileHistory(str(history_path)) if history_path else None
        )

    def _init_services(self, repo_root: Path) -> None:
        """初始化单例 Service 并注入 Context"""
        artifacts = ArtifactManager(repo_root)
        indexer = IndexService(repo_root)
        patch_engine = PatchEngine(repo_root)
        fetcher = CodeFetcher(repo_root)
        
        self.context = ServiceContext(
            repo_root=repo_root,
            artifacts=artifacts,
            indexer=indexer,
            patch_engine=patch_engine,
            fetcher=fetcher
        )
        # 将 context 注入 Agent
        self.agent = AutoPatchAgent(self.context)

    def run(self) -> int:
        self.renderer.print_panel(
            "AutoPatch-J V2: 极简 Java 安全补丁智能体\n输入 /help 查看命令，使用 @ 符号绑定上下文。",
            title="欢迎使用",
            style="bold blue"
        )
        
        if not self.repo_root:
            self.renderer.print_info("未检测到 Java 项目。请进入项目目录并执行 /init。")
        else:
            self.renderer.print(f"当前项目: [bold cyan]{self.repo_root}[/bold cyan]")

        while True:
            # --- 门禁检查：是否存在待确认补丁 ---
            pending = self.artifacts.load_pending_patch() if self.artifacts else None
            prompt_prefix = "autopatch-j"
            
            if pending:
                # 1. 渲染 Diff 预览
                self.renderer.print_diff(pending.diff, title=f" 预览: {pending.file_path} ")
                # 2. 渲染精美的浮动动作面板
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
                return 0

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

    def handle_chat(self, text: str) -> None:
        """处理自然语言对话及上下文注入，支持平滑的流式反馈"""
        if not self.agent:
            self.renderer.print_error("Agent 未就绪。请先执行 /init。")
            return

        # 1. 上下文注入：识别文本中的 @mention
        mentions = re.findall(r'@([\w\.]+)', text)
        extra_context = ""
        for m in mentions:
            entries = self.indexer.search(m, limit=1)
            if entries:
                entry = entries[0]
                code = self.fetcher.fetch_by_index_entry(entry)
                extra_context += f"\n--- 引用代码片段 ({entry.kind}: {entry.name}) ---\n{code}\n"
        
        final_prompt = text
        if extra_context:
            self.renderer.print_step(f"已自动注入 {len(mentions)} 处代码上下文...")
            final_prompt = f"{extra_context}\n--- 用户请求 ---\n{text}"

        # 2. 调用 Agent 决策并渲染平滑反馈
        self.renderer.print() # 留出一点空行
        
        # 使用 rich 的 status 管理底部的动态 Spinner
        with self.renderer.console.status("[bold yellow]Agent 正在思考修复路径...", spinner="dots") as status:
            
            def on_thought_token(token: str) -> None:
                # 以淡灰色斜体打印 Agent 的内心独白
                self.renderer.print(token, end="", style="dim italic")

            def on_tool_start(tool_name: str) -> None:
                # 动态更新底部的状态行文字
                # 这种原地刷新的效果能极大缓解用户焦躁
                status.update(f"[bold blue]正在执行工具: {tool_name} [此过程可能耗时数秒]...")

            # 执行 ReAct 循环
            self.agent.chat(
                final_prompt, 
                on_thought_token=on_thought_token, 
                on_tool_start=on_tool_start
            )
            
        self.renderer.print() # 结束后的换行

    def handle_command(self, raw_cmd: str) -> None:
        """处理斜杠指令"""
        parts = raw_cmd.split()
        cmd = parts[0].lower()

        if cmd == "/init":
            self.handle_init()
        elif cmd == "/status":
            self.handle_status()
        elif cmd == "/reindex":
            self.handle_reindex()
        elif cmd == "/help":
            self.renderer.print_info("常用命令:\n  /init     初始化当前目录为 Java 项目\n  /status   查看运行环境及扫描器状态\n  /reindex  重新扫描符号索引 (@mention)\n  /help     显示此帮助\n  /quit     退出程序")
        elif cmd in ("/quit", "/exit"):
            sys.exit(0)
        else:
            self.renderer.print_error(f"未知命令: {cmd}")

    def handle_init(self) -> None:
        self.renderer.print_step("正在初始化 AutoPatch-J 环境...")
        
        # 1. 确认项目根目录
        self.repo_root = self.cwd
        self.indexer = IndexService(self.repo_root)
        self.artifacts = ArtifactManager(self.repo_root)
        self.agent = AutoPatchAgent(self.repo_root)
        self.fetcher = CodeFetcher(self.repo_root)

        # 2. 安装/自检扫描器运行时
        from autopatch_j.scanners.semgrep import install_managed_semgrep_runtime
        status, msg = install_managed_semgrep_runtime()
        self.renderer.print_step(f"扫描器运行时自检: {status}")
        
        # 3. 建立符号索引
        self.renderer.print_step("正在建立符号索引（类与方法）...")
        stats = self.indexer.rebuild_index()
        
        self.renderer.print_success(f"初始化完成！索引项: {stats.get('total', 0)} (Java文件: {stats.get('file', 0)})")

    def handle_status(self) -> None:
        if not self.repo_root:
            self.renderer.print_info("尚未加载项目。")
            return
        
        stats = self.indexer.get_stats()
        status_text = (
            f"项目根目录: {self.repo_root}\n"
            f"大模型模型: {self.agent.label}\n"
            f"索引统计项: {stats}\n"
        )
        self.renderer.print_panel(status_text, title="系统状态")

    def handle_reindex(self) -> None:
        if not self.indexer: return
        self.renderer.print_step("正在重新构建索引...")
        stats = self.indexer.rebuild_index()
        self.renderer.print_success(f"索引刷新完成，共处理 {stats.get('total', 0)} 个项。")

    def handle_apply(self, pending: Any) -> None:
        engine = PatchEngine(self.repo_root)
        self.renderer.print_step(f"正在应用补丁至 {pending.file_path}...")
        
        if engine.apply_patch(pending):
            self.renderer.print_success("补丁物理应用成功！")
            
            # --- 自动化第三级门禁：语义重扫 ---
            from autopatch_j.core.validator_service import SemanticValidator
            from autopatch_j.scanners import get_scanner, DEFAULT_SCANNER_NAME
            
            scanner = get_scanner(DEFAULT_SCANNER_NAME)
            if scanner:
                self.renderer.print_step("正在执行语义验证（重新扫描）...")
                validator = SemanticValidator(self.repo_root, scanner)
                success, msg = validator.verify_fix(pending)
                
                if success:
                    self.renderer.print_success(msg)
                else:
                    self.renderer.print_error(msg)
            
            self.artifacts.clear_pending_patch()
        else:
            self.renderer.print_error("补丁应用失败，文件可能已被外部修改或查找匹配失效。")

    def handle_discard(self) -> None:
        if self.artifacts:
            self.artifacts.clear_pending_patch()
            self.renderer.print_info("已丢弃当前补丁草案。")

def main() -> int:
    # 引导入口，默认使用当前路径
    cli = AutoPatchCLI(Path.cwd())
    return cli.run()
