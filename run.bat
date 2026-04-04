@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==============================
echo 启动 Discord 机器人
echo 当前目录: %cd%
echo ==============================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 python，请先安装 Python 并加入 PATH。
    pause
    exit /b 1
)

if not exist bot.py (
    echo [错误] 当前目录下未找到 bot.py
    echo 请把本文件放到项目目录中，或修改 bat 中的启动文件名。
    pause
    exit /b 1
)

python bot.py

echo.
if errorlevel 1 (
    echo 程序已退出，返回码异常。
) else (
    echo 程序已正常退出。
)
pause