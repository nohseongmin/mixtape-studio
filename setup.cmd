@echo off
cd /d "%~dp0"
python -m venv --without-pip .venv
if errorlevel 1 goto :failed
python -m pip --python .venv\Scripts\python.exe install -r requirements.txt
if errorlevel 1 goto :failed
echo Setup complete. Open Start-Mixtape.cmd to launch.
pause
exit /b 0
:failed
echo Setup failed. Check Python 3.12 or newer, pip, and internet access.
pause
exit /b 1
