@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" app.pyw --selftest > selftest.out 2>&1
