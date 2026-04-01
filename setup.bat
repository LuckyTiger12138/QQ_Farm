@echo off
python --version >/dev/null 2>&1
if errorlevel 1 (
    echo Python not found
    pause
    exit /b 1
)
pip install -r requirements.txt
python create_shortcut.py
if not exist config.json echo {} > config.json
echo Done!
pause
