@echo off
REM Optional - the same processing exposed as an HTTP API on port 5001.
REM The Streamlit app does NOT need this; it calls the code directly.
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

"%PY%" api.py
