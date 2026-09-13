# Wayfare — Claude Code 프로젝트 설정

## 프로젝트 개요
APK를 입력받아 앱 화면 흐름을 Screen Map(화면 지도)으로 자동 추출하는 파이프라인.
GitHub: https://github.com/k1212gh/wayfare

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

### 서버 시작 (포트: 백엔드 8008 = vite 프록시 기본값 BACKEND_PORT, 프론트 5173)
```bash
# 0) 로컬 LLM (Ollama, 별도 창) — 모델은 %USERPROFILE%\.ollama 에 있어 재부팅 후에도 유지
%LOCALAPPDATA%\Programs\Ollama\ollama.exe serve
# 1) 백엔드 — 이 PC 는 Windows 스토어 python 에 httpx 가 없으므로 의존성 설치된 venv 로 실행
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --host 127.0.0.1 --port 8008
# 2) 프론트 — --host 없으면 127.0.0.1 만 바인딩돼 localhost(IPv6) 접속이 거부됨
cd dashboard/frontend && npx vite --port 5173 --host
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
3. 서버 상태 (8008, 5173 포트)
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

## 실험 플래그 (2026-09-12, 브랜치 exp/paper-techniques — 기본 off, 롤백: `git checkout baseline-v0`)
| env | 기법 | 근거 논문 |
|---|---|---|
| `WALK_FRONTIER=1` | 앱 전역 미탐색 액션 큐 + 관측 그래프 최단경로 복귀 (`stage3_walk/frontier.py`) | LLM-Explorer, MobiCom 2025 |
| `WALK_FRONTIER_PREEMPT=3` | 스톨 3회째에 inert 강제 클릭 대신 프런티어 이동 우선 (v2) | 메가커피 실측 |
| `WALK_FRONTIER_REPOSITION=1` | 현재 화면에서 갈 곳 없으면 Back→재실행으로 아는 화면에 선 뒤 재탐색 (v3, 권장 조합) | LLM-Explorer fault-tolerant path finder |
| `TARPIT_LLM_ESCAPE=1` (+API 키, `TARPIT_BUDGET` 기본 20) | 막힌 화면에서 텍스트 위젯 목록으로 LLM 탈출 액션 문의 (`stage3_walk/tarpit_escaper.py`) | UI Tarpit Escaping, arXiv 2604.06763 |
| `COALESCE_LEARNED=1` (+`pair_classifier_weights.json`) | Stage 6 병합에 학습형 쌍 분류기 Tier L (`stage6_screenmap/pair_classifier.py`) | arXiv 2606.16650 |

- A/B 실측: `PYTHONPATH=. python scripts/ab_walk_megacoffee.py --device <serial> --pull-from-device co.kr.waldlust.megacoffee`
- 분류기 학습: `PYTHONPATH=. python scripts/train_pair_classifier.py` (workspace 상태 파일 + experiments 라벨 필요)

### 메가커피 A/B 실측 (2026-09-12, SM-S908N, 변형당 15분/400이벤트)
| 변형 | 이벤트 | 고유 화면 | 전이 | 태스크 8개 중 | 스톨 강제클릭 | 하드리셋 |
|---|---|---|---|---|---|---|
| baseline (2회) | 291 / 242 | 50 / 64 | 77 / 83 | 4 / 4 | 125 / 73 | 3 / 3 |
| frontier v1 | 265 | 49 | 85 | 6 | 92 | 3 |
| frontier v2 | 226 | 63 | 99 | 6 | 44 | 3 |
| frontier v3 (덤프 수정 후) | 229 | 71 | 124 | 6 | 25 | 2 |

- v3 1차(16:32)는 `dumpsys activity top` 이 5초로 느려진 환경 문제로 오염(69 이벤트) → 패키지 지정 덤프로 수정 후 재실행.
- 실행 간 편차: baseline 고유 화면 50 vs 64. 화면 수 단독 비교보다 전이 수·스톨 클릭·태스크 도달이 일관된 신호.
- 결과 파일: `workspace/ab_report_*.json`, 로그 `workspace/ab_run*.log`.

## LLM 공급자 (2026-09-12) — 대시보드 "LLM 설정" 패널 또는 .env
| env | 값 | 설명 |
|---|---|---|
| `LLM_MODE` | `api` / `openai` / `off` / `cli` | api=Claude, openai=로컬 OpenAI 호환 서버(Ollama·LM Studio·llama.cpp·vLLM), off=라벨은 화면 텍스트만 |
| `LLM_BASE_URL` | `http://<다른PC IP>:11434/v1` | openai 모드 필수 |
| `LLM_MODEL_SCREEN` / `LLM_MODEL_VISION` | `gemma4:12b` / `qwen3.5:9b` | 텍스트 / 비전 모델 (정확도 우선 조합). Qwen3.5·Gemma 4 는 한 모델로 둘 다 가능 |
| `LLM_STAGE5_MODE` | 비우면 자동 (로컬→`vision_name`, api→`screenmap_annotate`) | `vision_name` = 스크린샷 자유 생성 + 후보 스냅 (88%), `grounded` = 텍스트 후보 선택만 (비전 모델 없을 때, 62~65%) |
| `LLM_STAGE5_VISION=1` | | grounded 뒤에 스크린샷 라벨러도 실행 |
| `LLM_TIMEOUT` | 로컬은 600 권장 | |

- API: `GET/PUT /api/settings/llm`, `POST /api/settings/llm/test`, `POST /api/settings/llm/discover` (host 의 11434/1234/3000/8080 탐지)
- 지원 서버: Ollama(`:11434/v1`), LM Studio(`:1234/v1`), Open WebUI(`:3000/api` + Bearer 키), 그 외 OpenAI 호환 (vLLM, llama.cpp)
- 이 PC 에 Ollama 0.34 설치됨 (2026-09-12, winget). 서버: `%LOCALAPPDATA%\Programs\Ollama\ollama.exe serve`. 받아둔 모델: qwen3.5:9b, gemma4:12b, qwen2.5:7b-instruct, qwen2.5vl:7b, qwen2.5:3b
- **모델 벤치 (docs/local_llm_benchmark.md, RTX 4070 SUPER 12GB, 2회)**: 후보 선택 lenient 정확도 1차/2차 — qwen3.5:9b 87%/74%, gemma4:12b 78%/82%, qwen2.5:7b 57%/56%, qwen2.5:3b 26% (무의미). 비전 오답 qwen3.5 0/0, gemma4 3/0. 속도 qwen3.5 가 텍스트 2.4~3배 빠름, 5.5GB → **기본 qwen3.5:9b**, 정확도 우선이면 gemma4:12b. 다음 개선은 모델 교체보다 Stage 4 후보 정제(아이콘 설명·알림 배너)와 오버레이 제목 후보.
- **라벨링 방식 비교 (docs/labeling_method_comparison.md, 2026-09-13)**: 텍스트 후보 선택은 LLM 없는 휴리스틱(65%)과 동률, 텍스트 자유 생성은 더 나쁨; **스크린샷 자유 생성이 74~82%** 로 유일하게 기준선을 넘음(qwen3.5 82%, gemma4 79%). 후보 선택의 실제 기여는 category 분류. → **구현됨**: Stage 5 로컬 기본 모드 `vision_name` (`stage5_annotate/vision_namer.py`, 스크린샷 자유 생성 + 후보 스냅), 실측 88% (34화면). 기본 모델은 사용자 결정(정확도 우선)으로 텍스트 gemma4:12b / 비전 qwen3.5:9b.
- **에이전트 적합성 (docs/agent_readiness_plan.md, docs/webview_handling.md)**: 계획 5개 중 #2 위젯 grounding·#4 오버레이 분리/중복 엣지 접기(frequency·selectors) 완료 — Stage 4 `widget_table.py` 가 원본 뷰의 rid/text/desc/bounds 를 노드 `widgets` 로, 탐색 엣지에 `selector{by,resource_id,content_desc,text,class,bounds}`. WebView 앱은 클릭 컨테이너(글자 없음)+자식 TextView 구조라 라벨 귀속 필수, 검색창은 EditText 가 아니라 힌트 텍스트로 감지(`editable_hint`), systemui 뷰(55%) 제거. #1 검색 프로브(`stage3_walk/search_probe.py`, LLM 검색어·유니코드 입력·결과/상세 캡처·`dynamic` 노드) 구현됨 — 실기기 검증 대기. 다음: #3 전제조건 → #5 API.
- Ollama 는 네이티브 `/api/chat` 자동 사용 (`llm_client.py`, `LLM_OLLAMA_NATIVE=0` 로 해제): `/v1` 경로는 thinking 모델(Qwen3.5/Gemma4)에 `think:false` 가 안 먹어 응답이 비고 10배 느림. 벤치 스크립트 `scripts/bench_local_labels.py`, 정답표 `docs/bench_gold_megacoffee*.json` (투어별).
- 자유 라벨은 `LLM_PICK_ALLOW_FREE=1` 일 때만 (기본 고르기 전용). 피커는 generic 버튼·24자 초과·지점명(`\S{3,}점`) pick 을 거부.
- 라벨 대체 체인(LLM 없이도): LLM 라벨 → 제목 텍스트 → 화면 첫 텍스트 후보 → 액티비티 짧은 이름. `label_source` 로 출처 기록.
- 그래프 뷰 기본: 가로 흐름(LR), 구조 엣지(contains/static_ref/global) 숨김, 배지는 정보가 있을 때만.
