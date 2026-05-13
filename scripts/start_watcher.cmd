@echo off
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "ROOT_DIR=%%~fI"
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%SCRIPT_DIR%watch_calls.ps1" -RootPath "%ROOT_DIR%"
