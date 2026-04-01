@echo off
chcp 65001 >nul
echo ========================================
echo   QQ 农场助手 - 一键安装脚本
echo ========================================
echo.

:: 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10+
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 检查 Python 环境...
for /f "tokens=2" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo   Python 版本：%PYTHON_VERSION%

:: 检查 pip
pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] pip 不可用，请检查 Python 安装
    pause
    exit /b 1
)

echo.
echo [2/4] 安装依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)

echo.
echo [3/4] 创建桌面快捷方式...
python create_shortcut.py
if errorlevel 1 (
    echo [警告] 快捷方式创建失败，可手动运行 python create_shortcut.py
)

echo.
echo [4/4] 初始化配置...
if not exist config.json (
    echo {} > config.json
    echo   已创建配置文件 config.json
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 下一步操作：
echo 1. 双击桌面上的 "QQ 农场助手" 快捷方式启动程序
echo 2. 首次使用请运行 python tools\template_collector.py 采集模板
echo 3. 或者运行 python tools\import_seeds.py 导入种子图片
echo.
pause
