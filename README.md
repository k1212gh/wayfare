# ScreenAtlas

APK를 입력받아 앱의 화면 흐름을 Screen Map로 자동 추출하고 웹에서 시각화하는 로컬 자동화 툴.

## 디렉토리 구조

```
screenatlas/
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
├── dashboard/                            # ── 웹 시각화 ──
│   ├── backend/
│   │   └── server.py                  #   FastAPI (15 endpoints)
│   └── frontend/
│       └── src/
│           ├── App.tsx                #   라우팅 (Dashboard ↔ Graph)
│           ├── Dashboard.tsx          #   APK 업로드 + Tour 관리 + 진행률
│           ├── ScreenMapView.tsx          #   React Flow 그래프 (스크린샷/경로)
│           ├── ScreenPanel.tsx         #   노드 상세 패널
│           ├── SearchFilter.tsx       #   검색/카테고리 필터
│           ├── main.tsx               #   엔트리포인트
│           └── tokens.css             #   디자인 토큰 (Inter, 4색)
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
├── requirements.txt                   #   Python 의존성
└── .gitignore
```

## 빠른 시작

```bash
# 의존성
pip install -r requirements.txt
cd dashboard/frontend && npm install

# 서버
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --port 8000
cd dashboard/frontend && npx vite --port 5173

# 브라우저: http://127.0.0.1:5173
```

## 환경 설정 (.env)

```
LLM_MODE=cli           # cli=Claude Code 로컬 | api=Anthropic API
WALK_MODE=tap     # tap=TapWalker | droidbot=DroidBot
CACHE_ENABLED=true     # 크로스앱 UI 패턴 캐싱
```
