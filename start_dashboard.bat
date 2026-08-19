@echo off
setlocal
cd /d "%~dp0"

where conda >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Cannot find conda. Please start from Anaconda Prompt or edit this file with your Anaconda path.
  pause
  exit /b 1
)

call conda activate py311
if errorlevel 1 (
  echo [ERROR] Cannot activate conda environment: py311
  pause
  exit /b 1
)

python -m streamlit run "%~dp0dashboard\app.py"
if errorlevel 1 (
  echo [ERROR] Streamlit failed to start. Run: pip install -r local-requirements.txt
)
pause
