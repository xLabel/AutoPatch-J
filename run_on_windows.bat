@echo off
setlocal
SET "ROOT_DIR=%~dp0"

:: 1. 编码对齐，防止中文乱码
chcp 65001 > nul

:: 2. Python 版本与环境检查
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10 or higher.
    pause
    exit /b 1
)

:: 3. 自动同步虚拟环境 (Isolation)
if not exist "%ROOT_DIR%.venv" (
    echo [BOOTSTRAP] Creating virtual environment...
    python -m venv "%ROOT_DIR%.venv"
)

:: 4. 自动同步依赖 (Automation)
echo [DEPENDENCY] Syncing project dependencies from pyproject.toml...
"%ROOT_DIR%.venv\Scripts\python.exe" -m pip install --quiet -e "%ROOT_DIR%."

:: 5. 校验系统环境变量 (Guidance)
if "%LLM_API_KEY%"=="" (
    echo [ERROR] LLM_API_KEY is not set in your system environment variables.
    echo Please set it in: System Properties -> Environment Variables.
    pause
    exit /b 1
)

:: 6. 进入 Demo 目录并启动 (Execution)
echo [START] Launching AutoPatch-J (Demo Mode)...
cd /d "%ROOT_DIR%examples\demo-repo"

:: 使用虚拟环境中的 Python 执行
"%ROOT_DIR%.venv\Scripts\python.exe" -m autopatch_j

if %ERRORLEVEL% neq 0 pause
endlocal
