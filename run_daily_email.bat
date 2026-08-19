@echo off
setlocal
cd /d "%~dp0"
call "C:\Users\hh483\anaconda3\condabin\conda.bat" activate py311
if errorlevel 1 exit /b 1
python -m dashboard.daily_email
exit /b %errorlevel%
