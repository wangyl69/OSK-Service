@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Collect-PanelInput.ps1" %*
if errorlevel 1 echo Collection incomplete. Keep the saved ZIP and error log.
pause
