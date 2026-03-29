@echo off
echo ================================================
echo   Dance Video Stitcher - Stopping services...
echo ================================================
echo.

:: Kill Python backend
taskkill /f /im python.exe /fi "WINDOWTITLE eq VS-Backend" >nul 2>&1

:: Kill Node/npm editor
taskkill /f /fi "WINDOWTITLE eq VS-Editor" >nul 2>&1
taskkill /f /im node.exe /fi "WINDOWTITLE eq VS-Editor" >nul 2>&1

:: Fallback: kill by port
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8765.*LISTENING"') do (
    echo Killing port 8765 (PID %%a)
    taskkill /f /pid %%a >nul 2>&1
)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":5173.*LISTENING"') do (
    echo Killing port 5173 (PID %%a)
    taskkill /f /pid %%a >nul 2>&1
)

:: Close the cmd windows by title
timeout /t 1 /nobreak >nul
taskkill /f /fi "WINDOWTITLE eq VS-Backend" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq VS-Editor" >nul 2>&1

echo.
echo Done.
pause
