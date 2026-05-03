@echo off
REM ScreenAtlas — 서버 시작 (Windows)
echo Starting ScreenAtlas servers...

REM .env 의 BACKEND_PORT 가 8008 이라 8000/8008 둘 다 정리
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTEN') do taskkill /PID %%a /F 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8008 ^| findstr LISTEN') do taskkill /PID %%a /F 2>nul

REM workspace 디렉토리 보장 + 이전 로그 archive
if not exist "%~dp0workspace" mkdir "%~dp0workspace"
if exist "%~dp0workspace\_backend.log" (
  for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set ts=%%I
  set ts=!ts:~0,8!_!ts:~8,6!
  ren "%~dp0workspace\_backend.log" "_backend.!ts!.log" 2>nul
)

REM Backend — stdout+stderr 을 파일로 redirect (이전 detached cmd 라 로그 사라짐)
REM 2026-04-29: backend 재시작 후 진단/디버깅용 로그를 파일에 남김
start "screenatlas-backend" /B cmd /c "cd /d %~dp0 && set PYTHONPATH=. && python -m uvicorn dashboard.backend.server:app --host 127.0.0.1 --port 8008 > workspace\_backend.log 2>&1"

REM Frontend
start "screenatlas-frontend" /B cmd /c "cd /d %~dp0\dashboard\frontend && npx vite --host 127.0.0.1 --port 5173 > %~dp0workspace\_frontend.log 2>&1"

timeout /t 3 /nobreak >nul
echo.
echo  Backend:  http://127.0.0.1:8008
echo  Frontend: http://127.0.0.1:5173
echo  Logs:     workspace\_backend.log  workspace\_frontend.log
echo.
echo  Press any key to stop...
pause >nul
taskkill /F /IM node.exe 2>nul
taskkill /F /IM python.exe 2>nul
