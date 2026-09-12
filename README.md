# Wayfare

앱의 APK 를 올리면 연결된 기기에서 앱을 직접 돌아다니며 **화면과 화면 사이의 전환을 지도**(Screen Map)로 만들어 주는 로컬 도구입니다.
정적 분석(manifest·DEX) → 기기 탐색(TapWalker) → 화면 군집화 → 지도 빌드 → 로컬 LLM 라벨링의 6단계 파이프라인과,
결과를 탐색하는 웹 대시보드(프로젝트 / 흐름 지도 / 기기 / 모델)로 구성됩니다.

- LLM 은 **로컬 모델 우선** — Ollama·LM Studio·Open WebUI 를 대시보드에서 골라 쓰고, 화면 이름은 화면에 실제로 보이는 텍스트 중에서 고릅니다 (`docs/local_llm_benchmark.md`).
- 탐색은 프런티어 큐 + 경로 재생(LLM-Explorer 계열)으로 막힌 화면에서 되돌아 나옵니다 (`docs/walk_improvements_report.md`).

## 출처 / 참고

- **차용 알고리즘**: `navigator/journey_planner.py`의 3-stage 경로 추론은 **PoG (Paths-over-Graph)** 논문 컨셉을 차용 (`journey_planner.py:1-7` 참고).
- **나머지 컴포넌트는 모두 자체 설계**: TapWalker (Unseen Scoring + 3단 Back-Gesture Ladder), 3-Level Screen Signature (L1-Authoritative), Activity Classifier (A/B/C 5규칙), Stage 6 9단계 ETL, 5-tier Coalesce Cascade (C → D → A → A+ → B), Manifest Scan + multi-polling, focus_mismatch 관용 모드, Framework-aware ViewTreeReader 4종.

## 디렉토리 구조

```
wayfare/
│
├── config.py                          # 설정 + .env 로딩
├── main.py                            # CLI 진입점 (run/resume/serve)
├── pipeline.py                        # 6단계 파이프라인 오케스트레이터
├── .env                               # 환경 설정 (LLM_MODE, WALK_MODE)
│
├── stage1_install/                 # ── Stage 1: APK 전처리 ──
│   ├── apk_validator.py               #   APK 유효성 검증 (magic bytes, ZIP)
│   └── metadata_extractor.py          #   메타데이터 추출 (androguard/aapt2)
│
├── stage2_manifest/                     # ── Stage 2: 정적 분석 ──
│   ├── manifest_parser.py             #   바이너리 AndroidManifest 파싱
│   ├── decompiler.py                  #   apktool 디컴파일 (fallback)
│   └── layout_analyzer.py             #   레이아웃 XML 분석
│
├── stage3_walk/                    # ── Stage 3: 동적 탐색 ──
│   ├── tap_walker.py              #   자체 탐색 엔진 (노벨티 스코어링)
│   ├── screen_signer.py                #   3-Level 화면 서명 (구조/pHash/GNN)
│   ├── walker_dispatcher.py             #   DroidBot/TapWalker 디스패처
│   ├── utg_parser.py                #   DroidBot UTG 출력 파서
│   └── activity_coverage.py            #   Activity 커버리지 계산
│
├── stage4_screens/              # ── Stage 4: 데이터 전처리 ──
│   ├── view_tree_cleaner.py                 #   UI XML 정제 (bounds/package 제거)
│   ├── screen_clusterer.py             #   structure_str 페이지 클러스터링
│   ├── screenshot_processor.py        #   스크린샷 리사이즈/포맷 변환
│   └── screen_card_builder.py             #   Screen Card 패키징
│
├── stage5_annotate/                        # ── Stage 5: LLM 분석 ──
│   ├── llm_client.py                  #   Claude CLI/API 클라이언트
│   ├── screen_analyzer.py             #   화면 분석 (purpose, elements)
│   ├── subflow_analyzer.py           #   서브그래프 도출
│   └── grounding_checker.py       #   widget_id/enum 검증
│
├── stage6_screenmap/                         # ── Stage 6: ScreenMap 생성 ──
│   ├── screenmap_builder.py               #   서브그래프 병합
│   ├── screenmap_validator.py             #   도달성/데드엔드/순환 검증
│   ├── screenmap_enricher.py              #   정적분석 보강 + back-edge
│   ├── screenmap_serializer.py               #   screen_map.json 직렬화
│   └── route_finder.py                 #   Dijkstra + K-shortest 경로 탐색
│
├── navigator/                           # ── 태스크 경로 추론 ──
│   └── journey_planner.py                #   PoG 3단계 (Topic BFS + LLM Ranking)
│
├── cache/                             # ── 크로스앱 캐싱 ──
│   └── widget_cache.py               #   SQLite UI 패턴 캐시
│
├── tracing/                           # ── 구조화 로깅 ──
│   ├── touch_log.py                         #   Touch Log 스키마
│   └── logger.py                      #   JSONL 트레이스 로거
│
├── dashboard/                            # ── 웹 대시보드 ──
│   ├── backend/
│   │   ├── server.py                  #   FastAPI 진입점
│   │   ├── api/                       #   tours · graph · upload · device · emulator · settings(LLM)
│   │   └── services/pipeline_service.py
│   └── frontend/src/
│       ├── App.tsx                    #   해시 라우팅 (#/ · #/flow/<id> · #/devices · #/models)
│       ├── app/                       #   AppState(폴링·액션) · Shell(레일+상단바) · icons
│       ├── pages/                     #   ProjectsPage · FlowPage · DevicesPage · ModelsPage
│       ├── dashboard/                 #   ProjectCard · DevicePicker · LiveDeviceMirror · types
│       ├── ScreenMapView.tsx          #   React Flow 캔버스 (레이아웃·간선·경로·플래너)
│       ├── ScreenPanel.tsx            #   우측 인스펙터 (스크린샷·설명·전환·UI 요소)
│       ├── graph/                     #   노드/간선 컴포넌트 · 범례 · 팔레트(colors.ts)
│       └── tokens.css                 #   Wayfare 디자인 시스템 (크림·포리스트 그린·호박)
│
├── tests/
│   └── test_pipeline.py               #   9개 통합 테스트
│
├── scripts/                           # ── 유틸리티 ──
│   └── notion_*.py                    #   Notion 페이지 내보내기
│
├── workspace/                         # ── 런타임 (gitignore) ──
│   └── {tour_id}/
│       ├── apk/                       #   원본 APK
│       ├── static/                    #   정적분석 결과
│       ├── dynamic/                   #   탐색 결과 (states, screenshots)
│       ├── analysis/                  #   LLM 분석 결과
│       └── output/                    #   최종 ScreenMap + 리포트
│
├── CONTEXT.md                         #   세션 맥락 (이어서 작업용)
├── pyproject.toml                     #   Python 의존성 + 빌드 설정
└── .gitignore
```

## 빠른 시작

```bash
# 의존성
pip install -e .[dev]
cd dashboard/frontend && npm install

# 로컬 LLM (권장: Ollama, 12GB VRAM — 텍스트 gemma4:12b + 비전 qwen3.5:9b)
ollama pull gemma4:12b qwen3.5:9b

# 서버 (백엔드 8008, 프론트 5173)
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --host 127.0.0.1 --port 8008
cd dashboard/frontend && npx vite --port 5173 --host

# 브라우저: http://localhost:5173  → 모델 탭에서 "서버 찾기" → 프로젝트 탭에서 APK 업로드 → 분석 시작
```

## 환경 설정 (.env)

```
LLM_MODE=openai                          # openai=로컬 OpenAI 호환 서버 | api=Claude API | off=LLM 없이
LLM_BASE_URL=http://127.0.0.1:11434/v1   # Ollama(11434) · LM Studio(1234) · Open WebUI(3000/api)
LLM_MODEL_SCREEN=gemma4:12b              # 텍스트(라벨 선택·플래너) — 정확도 우선 조합
LLM_MODEL_VISION=qwen3.5:9b              # 비전(스크린샷 라벨·탐색) — 비전은 qwen3.5 가 더 정확
LLM_STAGE5_VISION=1                      # 스크린샷 라벨러도 실행
WALK_FRONTIER=1                          # 프런티어 탐색 (권장 조합)
WALK_FRONTIER_PREEMPT=3
WALK_FRONTIER_REPOSITION=1
```
대시보드의 **모델** 탭이 같은 값을 읽고 씁니다.
