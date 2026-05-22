@echo off
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
python "%SCRIPT_DIR%watch_calls.py" --root-path "%ROOT_DIR%"
