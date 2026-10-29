@echo off
setlocal
SET "ROOT_DIR=%~dp0"

:: 1. 编码对齐，防止中文乱码
chcp 65001 > nul

:: 2. Python 版本与环境检查
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.14 or higher.
    pause
    exit /b 1
)

python -c "import sys; raise SystemExit(sys.version_info < (3, 14))"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python 3.14 or higher is required.
    pause
    exit /b 1
)

:: 3. 自动同步虚拟环境 (Isolation)
if not exist "%ROOT_DIR%.venv" (
    echo [BOOTSTRAP] Creating virtual environment...
    python -m venv "%ROOT_DIR%.venv"
)

"%ROOT_DIR%.venv\Scripts\python.exe" -c "import sys; raise SystemExit(sys.version_info < (3, 14))"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Existing .venv requires Python 3.14+. Recreate it with that interpreter.
    pause
    exit /b 1
)

:: 4. 自动同步依赖 (Automation)
echo [DEPENDENCY] Syncing project dependencies from pyproject.toml...
"%ROOT_DIR%.venv\Scripts\python.exe" -m pip install --quiet -c "%ROOT_DIR%constraints.txt" -e "%ROOT_DIR%."

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

:: 5. 校验系统环境变量 (Guidance)
if "%AUTOPATCH_LLM_API_KEY%"=="" (
    echo [ERROR] AUTOPATCH_LLM_API_KEY is not set in your system environment variables.
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
