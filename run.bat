@echo off
REM ScreenAtlas — 서버 시작 (Windows)
echo Starting ScreenAtlas servers...

REM Kill existing
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTEN') do taskkill /PID %%a /F 2>nul

REM Backend
start /B cmd /c "cd /d %~dp0 && set PYTHONPATH=. && python -m uvicorn dashboard.backend.server:app --host 127.0.0.1 --port 8000"

REM Frontend
start /B cmd /c "cd /d %~dp0\dashboard\frontend && npx vite --host 127.0.0.1 --port 5173"

timeout /t 3 /nobreak >nul
echo.
echo  Backend:  http://127.0.0.1:8000
echo  Frontend: http://127.0.0.1:5173
echo.
echo  Press any key to stop...
pause >nul
taskkill /F /IM node.exe 2>nul
taskkill /F /IM python.exe 2>nul
