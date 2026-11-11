@echo off
setlocal
SET "ROOT_DIR=%~dp0"

chcp 65001 > nul

call uv --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo 无法启动：未找到可用的 uv。安装指引：https://docs.astral.sh/uv/getting-started/installation/
    call :pause_if_interactive
    exit /b 1
)

:: uv 校验 Python、锁文件并准备项目环境；模型配置由 CLI 诊断。
cd /d "%ROOT_DIR%examples\demo-repo"
if errorlevel 1 (
    echo 无法启动：无法进入示例目录。
    call :pause_if_interactive
    exit /b 1
)

echo [START] Launching AutoPatch-J (Demo Mode)...
call uv run --project "%ROOT_DIR%." --locked --no-dev --no-python-downloads python -m autopatch_j

set "CLI_EXIT=%ERRORLEVEL%"
if not "%CLI_EXIT%"=="0" (
    echo AutoPatch-J 未能正常启动或关闭，请查看上方错误；需要已安装的 Python 3.14 或更高版本。
    call :pause_if_interactive
)
endlocal & exit /b %CLI_EXIT%

:pause_if_interactive
:: 零秒 timeout 检查控制台输入，重定向输入时立即返回非零。
timeout /t 0 >nul 2>&1
if not errorlevel 1 pause
exit /b 0
