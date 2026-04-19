# ScreenAtlas — Claude Code 프로젝트 설정

## 프로젝트 개요
APK를 입력받아 앱 화면 흐름을 Screen Map로 자동 추출하는 파이프라인.
GitLab: https://github.com/YOUR_GITHUB_ID/screenatlas (branch: feature/ScreenMap-POC)

## 환경
- Python 3.12, Node 24, Windows/Linux
- .env 파일에 ANTHROPIC_API_KEY, NOTION_TOKEN 등 설정
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
cd screenatlas
PYTHONPATH=. python -m uvicorn dashboard.backend.server:app --port 8000
cd dashboard/frontend && npx vite --port 5173
```

### 테스트
```bash
PYTHONPATH=. python tests/test_pipeline.py
```

### 노션 내보내기
```bash
python scripts/notion_detailed_report.py
```

## 슬래시 명령 (사용자가 요청 시 실행)

### /save — 작업 저장 + push
1. git add -A
2. git status로 변경 파일 확인
3. 변경 내용 요약하여 커밋 메시지 작성
4. git push
5. CONTEXT.md 업데이트

### /resume — 다른 기기에서 이어서 시작
1. git pull
2. CONTEXT.md 읽어서 현재 상태 파악
3. .env 존재 여부 확인 (없으면 안내)
4. 서버 상태 확인 (포트 8000, 5173)
5. ADB 디바이스 연결 확인
6. 마지막 커밋 내용 + TODO 상태 보여주기

### /status — 현재 상태 한눈에
1. git log --oneline -5 (최근 커밋)
2. git status (변경 파일)
3. 서버 상태 (8000, 5173 포트)
4. ADB 디바이스 연결
5. workspace/ 내 tour 목록 + 상태
6. .env 설정 확인 (API 키 유효 여부)

### /report — 노션에 진행 상황 정리
1. 현재 TODO/완료 항목 수집
2. 최근 커밋 이력 수집
3. 테스트 결과 실행
4. scripts/notion_detailed_report.py 실행
5. 결과 URL 출력

### /test — 전체 테스트
1. python tests/test_pipeline.py 실행
2. 실패 시 원인 분석
3. 결과 요약 출력

### /walk <apk_path> — APK 탐색
1. APK 유효성 검증
2. 서버 실행 중인지 확인 (아니면 시작)
3. API로 업로드 + Run
4. 진행 상태 모니터링
5. 완료 시 결과 요약 (노드/엣지/스크린샷 수)

### /logs — 로그 확인
1. /tmp/sa_backend.log 최근 30줄
2. workspace/*/dynamic/droidbot_log.txt 또는 walk.json 상태
3. 에러 있으면 원인 분석

### /env — 환경 점검
1. Python, Node, ADB 버전 확인
2. .env 파일 존재 + 키 유효성 (PLACEHOLDER 체크)
3. pip 패키지 설치 상태
4. npm 패키지 설치 상태
5. 디바이스 연결 상태
6. 문제 있으면 해결 방법 안내
