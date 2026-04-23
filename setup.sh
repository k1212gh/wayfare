#!/bin/bash
# ScreenAtlas — 새 기기 자동 셋업 스크립트
# 사용법: git clone 후 이 스크립트 실행
#   git clone https://github.com/YOUR_GITHUB_ID/screenatlas.git
#   cd screenatlas && git checkout feature/ScreenMap-POC
#   bash setup.sh

set -e
echo "========================================="
echo " ScreenAtlas Setup"
echo "========================================="

# 1. Python 의존성
echo "[1/5] Python dependencies..."
pip install -e .[dev] 2>/dev/null || pip install fastapi uvicorn androguard networkx Pillow lxml imagehash

# 2. DroidBot (optional)
echo "[2/5] DroidBot..."
pip install -e .[droidbot] 2>/dev/null || echo "  DroidBot skipped (optional)"

# androguard 4.x 호환 shim
python -c "
import androguard.core, os
d = os.path.join(os.path.dirname(androguard.core.__file__), 'bytecodes')
os.makedirs(d, exist_ok=True)
open(os.path.join(d,'__init__.py'),'w').close()
with open(os.path.join(d,'apk.py'),'w') as f:
    f.write('from androguard.core.apk import APK\n')
print('  androguard shim OK')
" 2>/dev/null || true

# 3. Frontend
echo "[3/5] Frontend dependencies..."
cd dashboard/frontend && npm install --silent 2>/dev/null && cd ../..

# 4. .env 생성 (없으면)
echo "[4/5] Environment..."
if [ ! -f .env ]; then
    cat > .env << 'ENVEOF'
# LLM
LLM_MODE=api
ANTHROPIC_API_KEY=sk-ant-PLACEHOLDER-replace-with-real-key
LLM_MODEL_SCREEN=claude-sonnet-4-5
LLM_MODEL_WIDGET=claude-haiku-4-5

# Walk
WALK_MODE=tap

# Cache
CACHE_ENABLED=true

# Notion (optional)
# NOTION_TOKEN=ntn_your_token_here
ENVEOF
    echo "  .env created — edit ANTHROPIC_API_KEY with real key"
else
    echo "  .env already exists"
fi

# 5. Demo data
echo "[5/5] Demo data..."
python create_demo.py 2>/dev/null || true

echo ""
echo "========================================="
echo " Setup complete!"
echo ""
echo " Start servers:"
echo "   PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --port 8000"
echo "   cd dashboard/frontend && npx vite --port 5173"
echo ""
echo " Browser: http://127.0.0.1:5173"
echo ""
echo " Device: adb devices"
echo "========================================="
