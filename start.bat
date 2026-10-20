@echo off
rem Debug: run with console to see errors
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" app.pyw
pause
