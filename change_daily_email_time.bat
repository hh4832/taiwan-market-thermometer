@echo off
setlocal
cd /d "%~dp0"
call "C:\Users\hh483\anaconda3\condabin\conda.bat" activate py311
if errorlevel 1 goto :error
python scripts\change_daily_email_time.py
if errorlevel 1 goto :error
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_daily_email_task.ps1"
if errorlevel 1 goto :error
echo.
echo The daily email time has been updated.
pause
exit /b 0

:error
echo.
echo Unable to update the daily email time.
pause
exit /b 1
