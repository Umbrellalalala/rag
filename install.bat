@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   本地文件索引 / RAG 助手 - 依赖安装脚本
echo   （自动检测 GPU，安装对应 torch）
echo ============================================
echo.

rem 优先使用 conda 的 rag 环境，否则使用当前 python
conda env list | findstr /C:"envs\rag" >nul
if %errorlevel%==0 (
    echo 使用 conda 环境：rag
    conda run -n rag python install.py %*
) else (
    echo 使用当前 Python 环境
    python install.py %*
)

echo.
pause
endlocal
