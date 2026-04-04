@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==============================
echo 安装/更新项目依赖
echo 当前目录: %cd%
echo ==============================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 python，请先安装 Python 并加入 PATH。
    pause
    exit /b 1
)

echo [1/3] 升级 pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [错误] pip 升级失败。
    pause
    exit /b 1
)

if exist requirements.txt (
    echo [2/3] 检测到 requirements.txt，正在安装依赖...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] requirements.txt 安装失败。
        pause
        exit /b 1
    )
) else (
    echo [2/3] 未检测到 requirements.txt，安装常用依赖...
    python -m pip install -U discord.py python-dotenv openai
    if errorlevel 1 (
        echo [错误] 依赖安装失败。
        pause
        exit /b 1
    )
)

echo.
echo [3/3] 依赖安装完成。
echo 现在你可以双击 run_discord_bot.bat 启动项目。
echo.
pause