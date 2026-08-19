@echo off
setlocal
cd /d "%~dp0"

set "CONDA_BAT=C:\Users\hh483\anaconda3\condabin\conda.bat"

if not exist "%CONDA_BAT%" (
  echo [ERROR] Cannot find Anaconda at:
  echo %CONDA_BAT%
  pause
  exit /b 1
)

call "%CONDA_BAT%" activate py311
if errorlevel 1 (
  echo [ERROR] Cannot activate conda environment: py311
  pause
  exit /b 1
)

start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://localhost:8501'"

python -m streamlit run "%~dp0dashboard\app.py" --server.address localhost --server.port 8501
if errorlevel 1 (
  echo [ERROR] Streamlit failed to start. Run: pip install -r local-requirements.txt
)
endlocal
