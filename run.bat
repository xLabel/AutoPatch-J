@echo off
setlocal
SET PYTHONPATH=%CD%\src
echo Starting AutoPatch-J V2.4...
python -m autopatch_j
if %ERRORLEVEL% neq 0 pause
endlocal
