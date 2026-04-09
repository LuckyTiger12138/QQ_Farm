@echo off
chcp 65001 >nul 2>&1
echo ========================================
echo   QQFarmBot EXE Build Script
echo ========================================
echo.

:: Clean old build
echo [1/3] Cleaning old build...
rmdir /s /q build 2>nul
rmdir /s /q dist\QQFarmBot 2>nul

:: Build
echo [2/3] Building QQFarmBot...
pyinstaller build.spec --clean --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed!
    pause
    exit /b 1
)

:: Verify
echo.
echo [3/3] Verifying...
if not exist "dist\QQFarmBot\QQFarmBot.exe" (
    echo [ERROR] QQFarmBot.exe not found!
    pause
    exit /b 1
)
if not exist "dist\QQFarmBot\_internal\templates" (
    echo [WARNING] templates directory missing!
)

for %%A in (dist\QQFarmBot\QQFarmBot.exe) do echo EXE: %%~zA bytes
echo Templates: %~dp0dist\QQFarmBot\_internal\templates

echo.
echo [OK] Build complete! Output: dist\QQFarmBot\
echo.
pause
