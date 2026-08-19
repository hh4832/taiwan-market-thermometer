@echo off
setlocal
cd /d "%~dp0"
call "C:\Users\hh483\anaconda3\condabin\conda.bat" activate py311
if errorlevel 1 goto :error

echo [1/4] Installing required packages...
python -m pip install -r local-requirements.txt
if errorlevel 1 goto :error

echo [2/4] Saving Gmail and FinLab credentials...
python scripts\setup_daily_email.py
if errorlevel 1 goto :error

echo [3/4] Sending a test email...
python -m dashboard.daily_email --send-test
if errorlevel 1 goto :error

echo [4/4] Creating the Windows scheduled task...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_daily_email_task.ps1"
if errorlevel 1 goto :error

echo.
echo Setup completed. Check your inbox for the test email.
pause
exit /b 0

:error
echo.
echo Setup failed. Review the message above; no password was written to Git.
pause
exit /b 1
