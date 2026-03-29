@echo off
echo ================================================
echo   Dance Video Stitcher - Stopping services...
echo ================================================
echo.

:: Stop backend (python main.py on port 8765)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765.*LISTENING"') do (
    echo Stopping backend (PID %%a)...
    taskkill /F /PID %%a >nul 2>&1
)

:: Stop editor (node/npm on port 5173)
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173.*LISTENING"') do (
    echo Stopping editor (PID %%a)...
    taskkill /F /PID %%a >nul 2>&1
)

:: Close the start.bat cmd windows (title contains VS-Backend / VS-Editor)
taskkill /fi "WINDOWTITLE eq VS-Backend*" /f >nul 2>&1
taskkill /fi "WINDOWTITLE eq VS-Editor*" /f >nul 2>&1

:: Also kill by window title pattern (python/node may change the title)
for /f "tokens=2" %%a in ('tasklist /v /fi "WINDOWTITLE eq Dance Video Stitcher" /fo list ^| findstr "PID:"') do (
    taskkill /F /PID %%a >nul 2>&1
)

echo.
echo Done. All services stopped.
timeout /t 2 /nobreak >nul
