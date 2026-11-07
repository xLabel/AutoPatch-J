@echo off
setlocal
SET "ROOT_DIR=%~dp0"

:: 1. 编码对齐，防止中文乱码
chcp 65001 > nul

:: 2. Python 版本与环境检查
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo 无法启动：未找到 Python，请安装 Python 3.14 或更高版本。
    call :pause_if_interactive
    exit /b 1
)

python -c "import sys; raise SystemExit(sys.version_info < (3, 14))"
if %ERRORLEVEL% neq 0 (
    echo 无法启动：需要 Python 3.14 或更高版本。
    call :pause_if_interactive
    exit /b 1
)

:: 3. 自动同步虚拟环境 (Isolation)
if not exist "%ROOT_DIR%.venv" (
    echo [BOOTSTRAP] Creating virtual environment...
    python -m venv "%ROOT_DIR%.venv"
    if ERRORLEVEL 1 (
        echo 无法启动：运行环境创建失败，请检查上方错误。
        call :pause_if_interactive
        exit /b 1
    )
)

"%ROOT_DIR%.venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 14))"
if %ERRORLEVEL% neq 0 (
    echo 无法启动：现有 .venv 的 Python 版本不符合要求，请重建环境。
    call :pause_if_interactive
    exit /b 1
)

:: 4. 自动同步依赖 (Automation)
echo [DEPENDENCY] Syncing project dependencies from pyproject.toml...
"%ROOT_DIR%.venv\Scripts\python.exe" -m pip install --quiet -c "%ROOT_DIR%constraints.txt" -e "%ROOT_DIR%."

if %ERRORLEVEL% neq 0 (
    echo 无法启动：依赖安装失败，请检查上方安装错误。
    call :pause_if_interactive
    exit /b 1
)

:: 5. 进入 Demo 目录并启动；配置故障由 CLI 报告并保留输入
echo [START] Launching AutoPatch-J (Demo Mode)...
cd /d "%ROOT_DIR%examples\demo-repo"

:: 使用虚拟环境中的 Python 执行
"%ROOT_DIR%.venv\Scripts\python.exe" -m autopatch_j

set "CLI_EXIT=%ERRORLEVEL%"
if not "%CLI_EXIT%"=="0" call :pause_if_interactive
endlocal & exit /b %CLI_EXIT%

:pause_if_interactive
:: 零秒 timeout 检查控制台输入，重定向输入时立即返回非零。
timeout /t 0 >nul 2>&1
if not errorlevel 1 pause
exit /b 0
