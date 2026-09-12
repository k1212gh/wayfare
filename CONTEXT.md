# Wayfare — 다른 기기에서 이어서 작업하기

## 빠른 시작 (새 기기)

```bash
# 1. Clone
git clone https://github.com/k1212gh/wayfare.git
cd wayfare

# 2. Setup (의존성 + .env + demo)
bash setup.sh        # Linux/Mac/Git Bash
# 또는
setup.bat            # Windows CMD

# 3. .env에 실제 API 키 설정
# ANTHROPIC_API_KEY=sk-ant-실제키

# 4. 서버 시작
run.bat              # Windows
# 또는
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --port 8000 &
cd dashboard/frontend && npx vite --port 5173 &

# 5. 브라우저: http://127.0.0.1:5173
```

## 작업 동기화 (기기 간 이동)

```bash
# 작업 끝날 때 (현재 기기)
git add -A && git commit -m "WIP: 작업 내용" && git push

# 다른 기기에서 이어서
git pull
# .env는 gitignore라 기기마다 별도 관리
```

## 현재 상태 (2026-04-17)

### 완료
- 6단계 파이프라인 (APK → 정적 → 탐색 → 전처리 → LLM → ScreenMap)
- TapWalker (자체 탐색 엔진, 노벨티 스코어링)
- 3-Level Screen Signature (구조/pHash/GNN)
- 웹 대시보드 (업로드, 진행률, 그래프 뷰어)
- 스크린샷 노드 (Show/Hide 토글, 폰 비율)
- 경로 하이라이팅 (Shift+클릭)
- PoG 태스크 경로 탐색 (/api/plan?task=...)
- 크로스앱 UI 패턴 캐싱 (SQLite)
- 엣지 가중치 (탐색 빈도 기반)
- Split APK 설치 (adb install-multiple)
- WebView/Compose 앱 대응
- E2E 파이프라인 완주 (Samsung Calendar, 7분)

### 알려진 이슈
1. TapWalker 탐색 깊이: 120초에 4~5 화면 (목표 8+)
2. WebView 앱(메가커피): clickable=false인 Compose 요소 감지 개선 중
3. activity 추출 일부 기기에서 unknown

### 환경 (.env) — 기기마다 별도
```
LLM_MODE=api
ANTHROPIC_API_KEY=sk-ant-실제키여기
WALK_MODE=tap
CACHE_ENABLED=true
NOTION_TOKEN=ntn_your_token
```

### 디바이스
- 테스트 기기: SM-S908N (Galaxy S22 Ultra)
- ADB 연결 확인: `adb devices`
- Split APK 설치: 자동 (adb install-multiple)

### 핵심 파일 (수정 빈도 높은 것)
- `stage3_walk/tap_walker.py` — 탐색 로직 (가장 자주 수정)
- `dashboard/backend/server.py` — API 서버 (엔드포인트 추가)
- `dashboard/frontend/src/ScreenMapView.tsx` — 그래프 UI
- `.env` — 환경 설정 (gitignore, 기기별)
- `stage6_screenmap/__init__.py` — ScreenMap 생성 (edge injection)

### GitHub
- Repo: https://github.com/k1212gh/wayfare
- Branch: `main`
- 최근 커밋: 10개

### 노션
- 프로젝트 정리: "플젝 정리" 페이지 하위
- 스크립트: `python scripts/notion_detailed_report.py` (NOTION_TOKEN 필요)
