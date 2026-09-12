# ScreenAtlas — Claude Code 프로젝트 설정

## 프로젝트 개요
APK를 입력받아 앱 화면 흐름을 Screen Map(화면 지도)으로 자동 추출하는 파이프라인.
GitHub: https://github.com/YOUR_GITHUB_ID/screenatlas

## 용어 사전 (코드 식별자 ↔ 개념)
| 식별자 | 개념 |
|---|---|
| `TapWalker` (`stage3_walk/tap_walker.py`) | 자체 동적 탐색 엔진 (Unseen Scoring + 3단 Back-Gesture Ladder) |
| `RevisitWalker` | 2차 재방문 탐색 (vision 보강) |
| `ScreenSigner` / `ScreenSignature` | 3-Level 화면 서명 (구조 / pHash / GNN, L1-Authoritative) |
| `ViewTreeReader` 4종 (`view_tree_readers/`) | XML / Compose / Flutter / RN 프레임워크별 뷰 트리 추출 |
| `ScreenCard` (`stage4_screens/screen_card_builder.py`) | LLM 입력용 화면 단위 패키지 |
| `ScreenMap` (`stage6_screenmap/`, `screen_map.json`) | 최종 산출물 그래프 |
| `Wireframe` | 정적 분석 기반 뼈대 그래프 (Static Wireframe) |
| `Coalesce Cascade` | 5단계 중복 화면 병합 (C → D → A → A+ → B) |
| `Manifest Scan` (`mixins/scan.py`) | 미방문 Activity 직접 실행 스캔 |
| `JourneyPlanner` (`navigator/journey_planner.py`) | PoG 스타일 3단계 태스크 경로 추론 |
| `WidgetCache` (`cache/widget_cache.py`) | 크로스앱 UI 패턴 SQLite 캐시 |
| `TouchLog` (`tracing/touch_log.py`) | JSONL 실행 기록 |
| `Tour` (`tour_id`, `workspace/{tour_id}/`, `/api/tours`) | APK 1건에 대한 분석 실행 단위 (앱 투어) |
| `screen_id` / `screen_*` | 캡처된 화면 식별자와 화면 집합 (`state_*.json`, `states/`, `state_str`, `structure_str` 는 DroidBot 호환 포맷이라 그대로 둠) |
| `Widget` (`widget_id`, `widgets`, `trigger_widget`) | 화면 안의 조작 가능한 UI 요소 (lxml `Element`, DOM API 이름은 별개) |

## ⚠️ Canonical 작업 폴더
- 이 파일이 위치한 디렉터리 (git 레포 루트)만이 유일한 canonical 소스
- 세션 시작·재개 시 `pwd`, `git rev-parse --show-toplevel`, 이 파일 위치가 일치하는지 확인

## 환경
- Python 3.12, Node 24, Windows/Linux
- .env 파일에 ANTHROPIC_API_KEY 등 설정
- 실기기(ADB) 또는 에뮬레이터 연결 필요 (탐색 시)

## 코드 규칙
- 모든 파일 I/O에 `encoding="utf-8"` 필수 (Windows CP949 방지)
- `json.dumps()`에 `encoding` 파라미터 넣지 마세요 (Python 3에 없음)
- LLM 호출은 API 모드 기본 (LLM_MODE=api), CLI는 deprecated
- 시크릿은 반드시 .env에 (코드에 하드코딩 금지)
- git push 전 사용자 확인 필요

## 자주 사용하는 명령

### 서버 시작
```bash
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --port 8000
cd dashboard/frontend && npx vite --port 5173
```

### 테스트
```bash
PYTHONPATH=. python -m pytest -q
```

## 슬래시 명령 (사용자가 요청 시 실행)

### /save — 작업 저장 + push
1. git add -A
2. git status로 변경 파일 확인
3. 변경 내용 요약하여 커밋 메시지 작성
4. git push
5. CONTEXT.md 업데이트

### /status — 현재 상태 한눈에
1. git log --oneline -5
2. git status
3. 서버 상태 (8000, 5173 포트)
4. ADB 디바이스 연결
5. workspace/ 내 tour 목록 + 상태
6. .env 설정 확인

### /test — 전체 테스트
1. PYTHONPATH=. python -m pytest -q 실행
2. 실패 시 원인 분석
3. 결과 요약 출력

### /walk <apk_path> — APK 탐색
1. APK 유효성 검증
2. 서버 실행 중인지 확인 (아니면 시작)
3. API로 업로드 + Run
4. 진행 상태 모니터링
5. 완료 시 결과 요약 (노드/엣지/스크린샷 수)

### /env — 환경 점검
1. Python, Node, ADB 버전 확인
2. .env 파일 존재 + 키 유효성 (PLACEHOLDER 체크)
3. pip / npm 패키지 설치 상태
4. 디바이스 연결 상태
