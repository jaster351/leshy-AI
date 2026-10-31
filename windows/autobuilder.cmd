@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0autobuilder-ui-v3.ps1" %*
endlocal
