@echo off
cd /d "%~dp0"
py -3 --version >nul 2>&1
if errorlevel 1 (
 echo Install Python 3.12 or newer from python.org, then run this installer again.
 pause
 exit /b 1
)
if not exist .venv py -3 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
 pause
 exit /b 1
)
echo Installation completed. Run Start-Nikan.cmd.
pause
