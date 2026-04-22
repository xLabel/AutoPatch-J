@echo off
setlocal
:: 获取脚本所在根目录（绝对路径）
SET "ROOT_DIR=%~dp0"
:: 设置 Python 模块搜索路径
SET "PYTHONPATH=%ROOT_DIR%src"

echo Starting AutoPatch-J (Demo Mode)...
echo Target: %ROOT_DIR%examples\demo-repo


:: 切换到示例项目目录
cd /d "%ROOT_DIR%examples\demo-repo"

:: 运行智能体
python -m autopatch_j

if %ERRORLEVEL% neq 0 pause
endlocal
