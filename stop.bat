@echo off
title Stop Dance Video Stitcher
echo ================================================
echo   Stopping Dance Video Stitcher...
echo ================================================
echo.

echo Checking port 8765 (backend)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765.*LISTENING"') do (
    echo   Killing PID %%a
    taskkill /f /t /pid %%a
)

echo Checking port 5173 (editor)...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173.*LISTENING"') do (
    echo   Killing PID %%a
    taskkill /f /t /pid %%a
)

echo.
echo Done.
echo Press any key to close...
pause >nul
