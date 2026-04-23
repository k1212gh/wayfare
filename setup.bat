@echo off
REM ScreenAtlas — Windows 자동 셋업
REM 사용법: git clone 후 이 스크립트 실행

echo =========================================
echo  ScreenAtlas Setup (Windows)
echo =========================================

echo [1/5] Python dependencies...
pip install -e .[dev] 2>nul || pip install fastapi uvicorn androguard networkx Pillow lxml imagehash

echo [2/5] DroidBot (optional)...
pip install -e .[droidbot] 2>nul || echo   DroidBot skipped

python -c "import androguard.core, os; d=os.path.join(os.path.dirname(androguard.core.__file__),'bytecodes'); os.makedirs(d,exist_ok=True); open(os.path.join(d,'__init__.py'),'w').close(); f=open(os.path.join(d,'apk.py'),'w'); f.write('from androguard.core.apk import APK\n'); f.close(); print('  shim OK')" 2>nul

echo [3/5] Frontend...
cd dashboard\frontend
call npm install --silent 2>nul
cd ..\..

echo [4/5] Environment...
if not exist .env (
    echo LLM_MODE=api > .env
    echo ANTHROPIC_API_KEY=sk-ant-PLACEHOLDER-replace-with-real-key >> .env
    echo LLM_MODEL_SCREEN=claude-sonnet-4-20250514 >> .env
    echo WALK_MODE=tap >> .env
    echo CACHE_ENABLED=true >> .env
    echo   .env created
) else (
    echo   .env exists
)

echo [5/5] Demo data...
python create_demo.py 2>nul

echo.
echo =========================================
echo  Setup complete!
echo.
echo  Start: run.bat
echo  Browser: http://127.0.0.1:5173
echo =========================================
