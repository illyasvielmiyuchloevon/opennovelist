@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if not errorlevel 1 goto run_python

where py >nul 2>nul
if not errorlevel 1 goto run_py

echo ?????? Python ???????? Python ??? python ? py ???
pause
exit /b 1

:run_python
python "%~dp0novel_workflow_cli.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:run_py
py -3 "%~dp0novel_workflow_cli.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:finish
if not "%EXIT_CODE%"=="0" (
    echo.
    echo ?????????????%EXIT_CODE%
    pause
)
exit /b %EXIT_CODE%