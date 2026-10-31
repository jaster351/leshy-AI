@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0autobuilder-ui.ps1" %*
endlocal
