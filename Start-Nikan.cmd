@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Run Install-Windows.cmd first.
 pause
 exit /b 1
)
.venv\Scripts\python.exe run.py
pause
