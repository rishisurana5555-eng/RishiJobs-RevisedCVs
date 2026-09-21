@echo off
REM Rishi Jobs CV Editor - starts the Streamlit app
cd /d "%~dp0"

REM Prefer the project virtual environment, fall back to whatever python is on PATH
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

"%PY%" -m streamlit run app.py
