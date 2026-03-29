@echo off
echo ================================================
echo   Dance Video Stitcher - Stopping services...
echo ================================================
echo.

set found=0

:: Kill by port 8765 (backend) - kill entire process tree
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8765.*LISTENING"') do (
    echo Stopping backend (PID %%a)...
    taskkill /f /t /pid %%a >nul 2>&1
    set found=1
)

:: Kill by port 5173 (editor) - kill entire process tree
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5173.*LISTENING"') do (
    echo Stopping editor (PID %%a)...
    taskkill /f /t /pid %%a >nul 2>&1
    set found=1
)

if %found%==0 (
    echo No services running.
) else (
    echo.
    echo Done. All services stopped.
)

pause
