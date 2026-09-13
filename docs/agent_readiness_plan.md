# 에이전트가 쓸 수 있는 지도 — 개선 계획 5개 (2026-09-13)

배경: 메가커피 지도(투어 358002fe)를 에이전트 관점에서 감사한 결과 —
경로(도달성 29/30, 전환 트리거 58/58)는 충분하지만 **위젯 grounding(30화면 중 10개, 텍스트·좌표 0)**,
**전제조건(0건)**, **검색·목록 같은 데이터 화면(자동 입력 0회, 검색창 미감지)** 이 비어 있다.
아래 5개를 순서대로 진행하면 "지도를 보고 목적지만 정하는" 수준에서 "지도만 보고 조작하는" 수준으로 간다.

| # | 항목 | 핵심 산출물 | 예상 | 완료 기준 (메가커피로 측정) |
|---|---|---|---|---|
| 1 | 검색·데이터 화면 처리 (AI 검색어) | 검색창 감지 → LLM 검색어 → 결과·상세 캡처, 결과 화면 템플릿 | 2일 | 매장 검색·메뉴 검색 결과 노드 + 상세 전환이 지도에 생김, 검색어가 엣지에 기록 |
| 2 | 위젯 grounding | 노드 위젯 표(rid/텍스트/desc/좌표/clickable), 엣지 셀렉터 객체 | 0.5일 | page 노드 100% 위젯 보유, 클릭 엣지 100% 셀렉터(좌표 단독 0) |
| 3 | 전제조건·상태 | `requires`/`condition`(로그인·장바구니·확인창) + 🔒 배지 | 1일 | 인증 백오프 4건이 노드 `requires: login` 으로 표시, 확인창 노드에 `blocks_parent` |
| 4 | 오버레이 분리 + 중복 엣지 병합 | dialog/sheet 를 부모와 분리(overlay 엣지), 같은 from→to 접기 + frequency | 0.5일 | 주문내역 확인창이 별도 노드, 중복 5쌍 → 1개 + frequency ≥2 |
| 5 | 에이전트 API | `locate`(현재 화면→노드), `next_action`(목표→셀렉터 포함 다음 액션) | 1일 | 실기기에서 "결제까지" 시나리오를 API 만으로 완주 |

## 1. 검색·데이터 화면 처리 — 검색 필요 화면 감지 시 AI 가 검색어 삽입 — ✅ 실기기 검증 완료 (2026-09-13, 4차)
구현: `stage3_walk/search_probe.py`(감지·LLM 검색어·실행·복귀 검증·전이 기록), `u2_helper.send_text`(uiautomator2 유니코드 입력, ASCII 는 adb 폴백),
워커 루프 1b3 훅(`SEARCH_PROBE=0` 으로 해제) + 검색 진입 우선 규칙(`seek_action`), `walk_transitions._annotate_dynamic_nodes`(결과 노드
`dynamic{kind, query_field, queries, item_action}`), Stage 4 결과/빈 결과 페이지 분리 + `semantic_merge` 검색 상태 병합 금지,
직렬화·대시보드(검색 결과/결과 없음/목록 배지, 인스펙터 "데이터 화면", 전환 "입력 … 후 검색"). 단위 테스트 17개 (`tests/test_search_probe.py`, `test_overlay_and_edge_fold.py`).

**실기기 측정 (메가커피, 투어 358002fe, `scripts/audit_agent_map.py`)** — 4차 탐색 30분:
| 항목 | 완료 기준 | 측정 |
|---|---|---|
| 검색 결과 노드 + 상세 전환 + 빈 결과 노드 | 매장 검색·메뉴 검색 각각 | 매장 검색: 결과 노드(검색어 3개, 행→상세 3건) + 빈 결과 노드 ✅ · 지도 탭 변형: 결과+빈 결과 ✅ · 퀵오더 매장 선택: 결과 → 매장 선택 ✅ · **메뉴 검색: 앱에 검색창 없음(해당 없음)** |
| 엣지에 검색어 | 남는다 | type_submit 엣지 5개(`input_value`, 빈 결과는 `outcome: empty`) ✅ |
| 자동 입력 실행 | ≥ 4 | 8회 (프로브 3회, 결과 8, 상세 5, 빈 결과 3, 실패 0) ✅ |
| 인증·결제 화면 진입 | 0 | 0 (인증 백오프 5, 결제 가드 6 — 모두 진입 전 차단) ✅ |

**실측에서 고친 것 (1차→4차)**: ① 힌트가 없는 WebView `EditText#keyword` 를 검색창으로 못 봄 → rid/위치 규칙 ② 단일 액티비티 앱이라 워커 Back 가드에
막혀 상세 뒤 복귀 실패 → 검색창 존재 검증 후에만 입력, 헤더 뒤로 버튼 → KEYCODE_BACK(앱 이탈 시 재실행, 탐색당 1회) ③ 화면 97% 바텀시트를 다이얼로그로
봐서 워커 stall·오버레이 오탐 → 90% 이상 시트는 페이지 ④ 같은 검색창을 화면 해시가 다를 때마다 재프로브 → 검색창당 1회 ⑤ 빈 결과 검색어를 먼저
(행 탭으로 시트가 닫히는 흐름 대비) ⑥ 결과/빈 결과 화면이 구조 해시·pHash 로 검색 화면에 병합됨 → Stage 4 outcome 별 페이지, coalesce 금지.
남은 한계: 검색 화면 진입은 탐색 분산에 좌우됨(3차는 힌트형 매장 정보를 못 만남 — 홈 하단 중앙 탭이 라벨 없음); 결과 행 선택이 가끔 행 안의 아이콘(즐겨찾기)을 탭함(제외 규칙 추가).

### 왜
검색 결과·목록은 입력값에 따라 내용이 바뀐다. 지금은 (a) 검색창을 못 찾고(WebView 입력은 EditText 로 안 잡힘),
(b) 찾아도 fixture 의 고정값("메뉴")을 넣으며, (c) 결과 화면을 "그냥 화면 하나"로 저장해 에이전트가 "무슨 검색어로 어떤 행을 눌러야 상세로 가는지" 알 수 없다.

### 설계
**감지 (Stage 3, `stage3_walk/search_probe.py` 신규)**
- 검색 가능 화면 판정: EditText/AutoComplete, 또는 WebView 안의 `View`/`EditText` 중 hint·desc·text 에 `검색|search|찾기|입력해 주세요` 가 있는 것, 또는 돋보기 아이콘(desc `검색`)이 있는 화면.
- 폼(이름·생년·전화·바코드)과 구분: 필드 3개 이상 + 제출 버튼이면 폼 → 5항 대상 아님(현행 fixture 값 유지), 필드 1~2개면 검색.

**검색어 생성 (LLM, 텍스트 모델)**
- 입력: 앱 이름·카테고리, 검색창 힌트("매장이나 지역명을 검색해 주세요"), 그 화면과 인접 화면의 텍스트 후보, 지금까지 지도에서 본 개체명(메뉴·매장·이벤트 이름).
- 출력 JSON: `{"queries": [{"text": "강남", "expect": "results"}, {"text": "아메리카노", "expect": "results"}, {"text": "zzqx", "expect": "empty"}]}` — 결과 있는 검색어 2개 + 빈 결과 1개(빈 상태 화면도 노드로 필요).
- LLM 없으면 fixture `sample_inputs` → 없으면 화면에서 본 개체명 1개 → 그래도 없으면 건너뜀. 프롬프트는 `docs/labeling_method_comparison.md` 의 grounding 원칙대로 "화면·지도에 있는 단어만".

**실행**
- 검색창 탭 → 기존 텍스트 지우기 → 입력 → Enter/검색 버튼 → 결과 캡처 → 첫 결과 행 탭 → 상세 캡처 → Back 2회. 검색어마다 반복. 폼 제출·결제·인증 화면이면 실행 안 함(현행 가드).
- 이벤트 기록: `{"event_type":"type_submit","field":<셀렉터>,"value":"강남"}`, 결과→상세는 `{"event_type":"click","list_item":true,"item_text":"…"}`.

**표현 (Stage 4/6)**
- 결과 노드: `dynamic: {"kind":"search_results","query_field":<셀렉터>,"queries":["강남","아메리카노"],"empty_state_node":"page_…"}`.
- 결과 행 템플릿(5항 리스트 모델과 공유): 같은 구조의 행을 묶어 `item_template: {"fields":[{"role":"title","bounds_rel":…},{"role":"subtitle"…}],"sample_items":["화성향일고점","…"],"action":{"to":"page_상세","by":"item_text"}}`.
- 엣지: 검색 화면→결과 `trigger_action: "type_submit"`, `input_value`, 결과→상세 `trigger_action: "click"`, `list_item: true`.

**대시보드**: 결과 노드에 "검색 결과" 배지, 인스펙터에 검색어 샘플·행 템플릿·빈 결과 링크.

### 완료 기준
- 메가커피: 매장 검색(지도 탭)·메뉴 검색이 각각 결과 노드 + 상세 전환 + 빈 결과 노드로 지도에 생기고, 엣지에 검색어가 남는다.
- 자동 입력 실행 횟수 ≥ 4 (검색어 3 × 검색창 ≥ 1 + 폼 1), 인증·결제 화면 진입 0.

## 2. 위젯 grounding — ✅ 구현됨 (2026-09-13, `stage4_screens/widget_table.py`)
결과(메가커피 재빌드): page 노드 위젯 보유 10/30 → **29/29**, 위젯 752개 전부 좌표·686개 텍스트/desc, 입력 필드 7개(웹 검색창 포함).
탐색 엣지 셀렉터 52개: resource_id 12 · content_desc 10 · text 23 · 좌표만 7 (이전 좌표만 10). WebView 특성 대응은 `docs/webview_handling.md`.
- Stage 4 `view_tree_cleaner` 가 버리는 `resource_id / text / content_description / bounds / clickable / class` 를 노드 `widgets` 에 그대로 싣는다(클릭 가능 + 텍스트 있는 것 우선, 최대 60개).
- 엣지 `trigger_widget` 문자열을 `selector: {"rid":…, "desc":…, "text":…, "class":…, "bounds":[…]}` 객체로. 우선순위 rid > desc > text > 좌표.
- 완료 기준: 30/30 page 노드 위젯 보유, 클릭 엣지 55개 중 좌표만 있는 것 0 (원본 상태에 rid 35개/85뷰 있음).

## 3. 전제조건·상태
- 탐색 로그의 인증 백오프(`auth_backoff_count`)·외부 가드·빈 장바구니 실패를 노드 `requires: ["login"]`, 엣지 `condition: "cart_nonempty"` 로.
- 화면 텍스트 기반 추론(LLM, 텍스트 모델): "로그인이 필요합니다", "장바구니가 비어 있습니다" 류 → `requires`.
- 확인창 노드: `blocks_parent: true` (닫아야 부모 조작 가능).
- 대시보드: 🔒 배지, 경로 묻기 결과에 "먼저 로그인" 단계 삽입.

## 4. 오버레이 분리 + 중복 엣지 병합 — ✅ 구현됨 (2026-09-13)
결과(메가커피 재빌드): 관측 엣지 62개 중 frequency>1 36개(이전 전부 0), 대체 셀렉터 보유 18개, 같은 from→to 중복 5쌍 → 0(kind 가 다른 2쌍만 남음).
바텀시트 2개(매장 정보·매장 상세)가 `is_dialog`/`blocks_parent` 노드로 분리되고 `overlay` 엣지로 연결. 영수증 모달은 별도 노드로 유지(주문내역과 병합 안 됨).
구현: `detect_dialog` 에 `touch_outside`/`design_bottom_sheet` id 추가 → Stage 4 page → 노드 `is_dialog`; `_is_mergeable` 에 오버레이≠일반 가드;
`_inject_walk_transitions` 가 같은 from→to 를 접어 `frequency`/`selectors[]` 누적, coalesce 후 `_rewrite_edges` 가 다시 접음(대표 셀렉터는 rid>desc>text>bounds).
남은 것: 웹 div 모달(MY매장 확인창)은 구조로 못 잡음 — 비전 category=dialog 로만 분리됨. #3 에서 화면 텍스트 기반 판정 추가.
- coalesce 에서 `is_dialog` 상태는 부모와 합치지 않고 `overlay` 엣지로 잇는다(지금 "주문내역"에 확인창 열림/닫힘이 합쳐짐).
- 같은 from→to 의 walk 엣지는 하나로 접고 `frequency`, `selectors[]` 누적 (현재 5쌍 중복, frequency 전부 0).

## 5. 에이전트 API
- `POST /api/tours/{id}/locate` — 현재 뷰 계층(uiautomator dump) 또는 스크린샷 → `structure_str`/pHash 로 가장 가까운 노드 + 신뢰도.
- `POST /api/tours/{id}/next_action` — `{current_node, goal}` → 경로 탐색기(`route_finder`) + 셀렉터로 "다음에 누를 것" 1개(`{"selector":…, "expect_node":…, "requires":[…]}`).
- 완료 기준: 실기기에서 "홈 → 매장 검색 '강남' → 매장 상세" 를 API 응답만으로 완주하는 스크립트(`scripts/agent_demo.py`).

## 순서와 의존
2(위젯) → 4(오버레이/중복) → 1(검색) → 3(전제조건) → 5(API). 2 가 먼저인 이유: 1·3·5 가 전부 셀렉터를 전제로 한다.
1 은 사용자 요청 우선순위이므로 2 직후에 착수. 총 5일 규모.
