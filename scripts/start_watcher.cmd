@echo off
chcp 65001 >nul
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
pushd "%ROOT_DIR%"
"%PYTHON_EXE%" "%SCRIPT_DIR%watch_calls.py" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd
exit /b %EXIT_CODE%
