# ScreenAtlas — 대화 맥락 & 작업 이력 (2026-04-16)

## 이 문서의 목적
다른 기기/세션에서 이어서 작업할 때 전체 맥락을 빠르게 파악하기 위한 문서.

---

## 프로젝트 한 줄 요약
APK -> 정적분석 -> DroidBot/TapWalker 동적탐색 -> LLM(Claude CLI) 분석 -> Screen Map 생성 -> 웹 시각화

## 현재 상태 (2026-04-16 22:00 기준)

### 완료된 것
- 6단계 파이프라인 전체 코드 (30+ Python 파일, 5 TSX 파일)
- 전문가 리뷰 MUST 8건 + SHOULD 14건 전수 수정
- TapWalker (자체 탐색 엔진, DroidBot 대체)
- 3-Level Screen Signature (구조해시 + pHash + HashGNN)
- 크로스앱 UI 패턴 캐싱 (SQLite)
- 경로 탐색 API (Dijkstra + K-shortest)
- 대시보드 UX (6단계 진행률 바)
- 실기기(SM-S908N) 연동 테스트 완료
- 노션 3페이지 기록

### 알려진 이슈 (해결 필요)
1. **Windows 인코딩**: `read_text()`/`write_text()`에 반드시 `encoding="utf-8"` 필요. 한글 경로 포함 시 CP949 충돌.
2. **TapWalker 탐색 깊이**: 60초 테스트에서 3 화면만 발견. 탐색 루프 개선 완료했으나 재테스트 필요.
3. **Git Bash 경로 변환**: ADB 경로에 `/sdcard/`가 Git Bash에서 `C:/Program Files/Git/sdcard/`로 변환됨. `//sdcard//` 이스케이프로 해결.
4. **DroidBot 접근성**: 실기기에서 `-accessibility_auto` + droidbotApp.apk 수동 설치 필요.

### 환경 (.env)
```
LLM_MODE=cli           # Claude Code CLI (API 키 불필요)
WALK_MODE=tap     # TapWalker (DroidBot 대신)
CACHE_ENABLED=true     # 크로스앱 UI 패턴 캐싱
```

### 서버 실행
```bash
cd screenatlas
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --port 8000  # 백엔드
cd dashboard/frontend && npx vite --port 5173  # 프론트엔드
# http://127.0.0.1:5173
```

### 디바이스
- SM-S908N (Galaxy S22 Ultra), serial: R5CT20G1ZFL
- ADB 연결 확인: `adb devices`

---

## 아키텍처

```
APK 입력
  -> Stage 1: APK 전처리 (androguard 메타데이터)
  -> Stage 2: 정적분석 (바이너리 Manifest 파싱)
  -> Stage 3: 동적탐색 (TapWalker + 3-Level Hashing)
  -> Stage 4: 데이터 전처리 (XML 정제 + 클러스터링)
  -> Stage 5: LLM 분석 (Claude CLI + 패턴 캐시)
  -> Stage 6: ScreenMap 생성 (검증 + 직렬화)
  -> 웹 시각화 (React Flow + dagre + 경로 탐색)
```

## 핵심 파일
- `stage3_walk/tap_walker.py` — 자체 탐색 엔진 (가장 중요)
- `stage3_walk/screen_signer.py` — 3-Level 해싱
- `cache/widget_cache.py` — SQLite 크로스앱 캐시
- `stage6_screenmap/route_finder.py` — Dijkstra 경로 탐색
- `dashboard/backend/server.py` — FastAPI 서버 (업로드/실행/API)
- `dashboard/frontend/src/Dashboard.tsx` — 대시보드 (진행률 바)
- `dashboard/frontend/src/ScreenMapView.tsx` — React Flow 그래프

## 레퍼런스 논문
1. ScreenAtlas (2601.17418)
2. MobileGPT (2312.03003)
3. ScreenMap-RAG (2509.00366)
4. 연구보고서 (A*Net + AgentTrace + PoG + MobileGUI-RL)

## 노션 페이지
- 전체 진행: https://www.notion.so/3445b1921b6e81f69a0fc02cb4871fca
- 트러블슈팅: https://www.notion.so/3445b1921b6e81ae8142e63b818186ed
- Phase 2 고도화: https://www.notion.so/3445b1921b6e81e3b31eea1a5acc9d40

## 남은 작업 (우선순위)
1. 전체 E2E 파이프라인 테스트 (업로드 -> 탐색 -> ScreenMap -> 시각화)
2. 프론트 경로 하이라이팅
3. pHash 단독 매칭 테스트
4. PoG식 자연어 태스크 경로 탐색
5. JSONL 구조화 로깅 전체 연동
