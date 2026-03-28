@echo off
title Dance Video Stitcher
cd /d "%~dp0"
echo ================================================
echo   Dance Video Stitcher
echo   Starting services...
echo ================================================
echo.

:: Start Python backend
echo [1/2] Starting backend (port 8765)...
start "VS-Backend" /min cmd /c "cd /d %~dp0py-backend && python main.py"

timeout /t 2 /nobreak >nul

:: Start FreeCut editor
echo [2/2] Starting editor (port 5173)...
start "VS-Editor" /min cmd /c "cd /d %~dp0 && npm run dev"

timeout /t 3 /nobreak >nul

:: Open browser
echo Opening browser...
start http://localhost:5173

echo.
echo ================================================
echo   Editor:  http://localhost:5173
echo   Backend: http://localhost:8765
echo   Close this window to stop all services.
echo ================================================

pause >nul
taskkill /fi "WINDOWTITLE eq VS-Backend" >nul 2>&1
taskkill /fi "WINDOWTITLE eq VS-Editor" >nul 2>&1
