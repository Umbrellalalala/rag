@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   本地文件索引 / RAG 助手 - 一键打包脚本
echo ============================================
echo.

echo [1/3] 检查并安装打包依赖...
python -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo 依赖安装失败，请检查 Python 环境。
    pause
    exit /b 1
)
echo.

echo [2/3] 开始打包...
python build.py %*
if errorlevel 1 (
    echo 打包失败，请查看上方错误信息。
    pause
    exit /b 1
)
echo.

echo [3/3] 完成！
echo 输出目录：%~dp0dist
echo.
pause
endlocal
