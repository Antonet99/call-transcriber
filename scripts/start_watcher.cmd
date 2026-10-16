@echo off
setlocal EnableDelayedExpansion
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Call Transcriber
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
pushd "%ROOT_DIR%"
rem Preserve literal arguments and capture Python's exit code before CALL.
"%PYTHON_EXE%" -X utf8 -m scripts.watch_calls %* & endlocal & set "EXIT_CODE=!errorlevel!" & call :finish
exit /b %EXIT_CODE%

:finish
popd
exit /b %EXIT_CODE%
