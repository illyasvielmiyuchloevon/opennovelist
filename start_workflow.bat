@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0novel_workflow_cli.py" %*
    goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0novel_workflow_cli.py" %*
    goto :eof
)

echo 未找到可用的 Python 解释器，请先安装 Python 并确保 python 或 py 可用。
pause
