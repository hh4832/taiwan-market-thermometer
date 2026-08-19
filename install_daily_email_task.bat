@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_daily_email_task.ps1"
if errorlevel 1 goto :error
echo.
echo The daily email task is ready.
pause
exit /b 0

:error
echo.
echo Unable to create the Windows scheduled task.
pause
exit /b 1
