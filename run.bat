@echo off
REM Rishi Jobs CV Branding Tool - starts the Flask API and the Streamlit app
cd /d "%~dp0"

REM Prefer the project virtual environment, fall back to whatever python is on PATH
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

start "Rishi Jobs CV API" cmd /k "%PY%" api.py
timeout /t 3 /nobreak >nul
"%PY%" -m streamlit run app.py
