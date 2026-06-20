@echo off
setlocal
cd /d "%~dp0"

echo ================================
echo   Outlook Token Tool
echo ================================

:: 精确清理运行 outlook_token_gui.py 的进程
echo 正在清理旧的 Outlook Token Tool 进程...
for /f "tokens=2 delims==" %%a in ('wmic process where "commandline like '%%outlook_token_gui.py%%'" get processid /format:value 2^>nul ^| findstr "ProcessId"') do (
    set "PID=%%a"
    call set "PID=%%PID: =%%"
    if defined PID if not "%%a"=="" (
        echo   终止 PID: %%a
        taskkill /PID %%a /F >nul 2>nul
    )
)

:: 清理占用端口 8765 的进程（仅 LISTENING）
echo 正在检查端口 8765...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING" 2^>nul') do (
    echo   终止占用端口 PID: %%p
    taskkill /PID %%p /F >nul 2>nul
)

echo 清理完成，启动程序...
timeout /t 1 /nobreak >nul

:: 启动
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0outlook_token_gui.py"
    exit /b 0
)

python "%~dp0outlook_token_gui.py"
pause
