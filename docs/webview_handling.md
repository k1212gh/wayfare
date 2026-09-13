# WebView 화면 대응 정리 (2026-09-13)

메가커피처럼 화면 대부분이 `MainActivity` 하나 안의 WebView 인 앱이 많다(커머스·프랜차이즈·금융).
탐색·지도·에이전트 세 단계에서 WebView 가 네이티브와 어떻게 다르고, 무엇을 어떻게 처리하는지 실측 기준으로 정리한다.

## 1. 접근성 트리에서 WebView 는 어떻게 보이나 (SM-S908N, 메가커피 실측)

uiautomator 덤프에서 `android.webkit.WebView` 노드는 **0개**였다 — 렌더링된 DOM 이 네이티브 클래스로 투영된다:

| DOM | 접근성 트리에 보이는 것 | 예 |
|---|---|---|
| `<div onclick>` / `<a>` / `<button>` | 클릭 가능한 `View` (텍스트 없음) + 그 안에 클릭 불가 `TextView`(글자) | `View[70,457][1370,614] click=1` + `TextView "매장이나 지역명을 검색해 주세요."` |
| `<input>` (포커스 전) | **EditText 가 아니라** 클릭 가능한 `View` + 플레이스홀더 `TextView` | 검색창, 바코드 입력 |
| `<input>` (포커스 후) | `EditText` 로 바뀜 | 이름·생년 입력 폼 |
| `<img alt>` / 아이콘 버튼 | `ImageView` 또는 `View` + `content_desc` (alt) | `ImageView desc="즐겨찾기" click=1`, `View desc="필터아이콘"` |
| 리스트 행 | 같은 구조의 형제 그룹이 반복 (행마다 이름·주소·시간·거리) | 매장 목록 3행 |
| resource-id | 거의 없음 (앱 자체 컴포넌트에만) | `touch_outside`, `clock` |
| `<input>` (힌트가 a11y 로 안 나오는 경우) | `EditText` 인데 text·hint·desc 모두 빈 문자열, id 만 남음 | 매장 정보의 `EditText#keyword` — 화면 제목에도 "검색" 없음 |
| 페이지 흐름을 담은 BottomSheetDialog | `touch_outside` + `design_bottom_sheet` 가 화면 97% 를 덮음 (매장 정보→검색→상세 전체) | 다이얼로그 id 만 보고 오버레이로 분류하면 안 됨 |
| 웹 div 모달 (`<div class="modal">`) | 아무 id·클래스 신호 없음 — 그냥 뷰들 | 퀵오더 시트, "메뉴를 선택해주세요 / 확인" 알림 |

그리고 전체 뷰의 **55% 가 `com.android.systemui`** (상태바 알림 아이콘 30여 개, Edge 패널) 였다 — 앱 뷰가 아닌데 텍스트 후보·위젯 표를 오염시킨다.

이 세 특성이 기존 코드에서 만든 문제:
- 위젯 추출이 `clickable` 플래그만 보고 텍스트 없는 컨테이너만 담아 → 위젯에 글자·좌표가 없었다 (30화면 중 10개, 텍스트 0).
- 검색창을 `EditText` 로만 찾아 → 검색 자동 입력 0회.
- 상태바 알림 desc("Claude 알림:") 가 제목 후보·위젯에 섞였다.

## 2. 단계별 대응

### 2-1. 탐색 (Stage 3, TapWalker)
현재 잘 되는 것: 클릭 가능한 `View` 컨테이너를 탭하므로 이동 자체는 된다(관찰 전이 58개, 텍스트 라벨 45개).
고칠 것:
- **검색창 감지**: `EditText` 외에 "클릭 가능 컨테이너 + 힌트 텍스트(검색/입력해 주세요/찾기)" 패턴을 입력 필드로 본다 → `widget_table.editable_hint` (구현됨, 2-2).
  탭하면 포커스되어 `EditText` 로 바뀌므로, 탭 → 0.4초 → 입력 순서.
- **한글 입력**: `adb shell input text` 는 ASCII 만. 현재 한글 경로는 `am broadcast -a clipper.set` 인데 이는 Clipper 앱이 깔려 있어야 동작한다 → 실기기에서 조용히 실패.
  대체: uiautomator2 의 `d.send_keys(text)` (자체 FastInputIME, 유니코드 OK) 또는 `d(focused=True).set_text()`. 계획 #1(검색어 AI) 구현 시 함께 교체.
- **제출**: 웹 검색 폼은 Enter(KEYCODE_66) 로 대부분 제출되지만, 돋보기 버튼만 있는 폼은 힌트 옆 `View desc="검색"` 을 탭해야 한다 → 입력 후 "제출 후보"(같은 y 구간의 클릭 가능 아이콘) 탭 규칙.
- **외부 웹 이탈**: 인스타그램·유튜브·구글플레이 페이지가 앱 안 WebView/CustomTab 으로 열린다. 패키지 가드(`_check_app_bounds`)가 CustomTab 은 잡지만 앱 내부 WebView 로 열린 외부 사이트는 못 잡는다 → 화면 텍스트에 앱 도메인과 무관한 브랜드(Instagram/YouTube/Play 스토어)가 보이면 `is_external: true` 로 표시하고 더 파고들지 않는다(계획 #3 전제조건과 함께).
- **Back 이 안 먹는 단일 액티비티**: 앱 전체가 `MainActivity` 하나라 워커의 Back 가드("메인 액티비티에서 Back 금지 — 앱이 종료됨")가 모든 Back 을 막는다.
  WebView 앱은 `onBackPressed → webview.goBack()` 으로 페이지를 되돌리므로 실제로는 안전한 경우가 많다. 검색 프로브는 (1) 워커 Back → (2) 헤더의 뒤로 버튼(상단 12%·좌측 15% 안의 클릭 뷰, 또는 desc 뒤로/이전/닫기) →
  (3) KEYCODE_BACK 후 포그라운드 패키지 확인(앱을 벗어나면 재실행) 순으로 되돌아가고, **되돌아왔는지를 검색창(같은 id 또는 좌표 겹침)으로 검증**한 뒤에만 다음 검색어를 친다 (`search_probe._return_to_search`).
  3차 실측 전(2차)에는 이 검증이 없어 상세 화면의 지도 위 "NAVER" 를 결과 행으로 탭하고, 퀵오더 시트에 검색어를 쳐서 이벤트 상세로 흘러갔다.
- **전체 화면 시트 = 페이지**: `design_bottom_sheet` 높이가 화면의 90% 이상이면 `detect_dialog` 가 False (`view_tree_parser`). 2차 실측에서 이 시트를 다이얼로그로 보는 바람에 워커가 매장 상세를 22번 "닫으려" 하다 stall 했고, 지도에 매장 정보·검색·상세가 오버레이로 찍혔다.
- **스크롤 리스트**: 행이 반복되는 화면은 스크롤로 새 행만 나오고 화면은 같다 → `infinite_scroll` 마킹은 있으나 탐색이 행마다 탭해 전이를 중복 생성(같은 from→to 4개). 행 템플릿(2-2)이 생기면 "행 1개만 탭" 규칙으로 줄인다.

### 2-2. 지도 (Stage 4/6) — 이번에 구현한 것 (`stage4_screens/widget_table.py`)
- **패키지·상태바 필터**: `package != 앱` 또는 상단 3.5% 영역 뷰 제거.
- **라벨 귀속**: 텍스트 없는 클릭 컨테이너 ← 안에 있는 가장 위쪽 텍스트. 면적이 가장 작은 컨테이너부터 귀속해 바깥 레이아웃이 삼키지 않게, 화면의 60% 이상을 덮는 `touch_outside` 류는 제외.
- **입력 필드 판정**: `EditText` 이거나 (클릭 컨테이너 + 힌트 패턴) → `editable: true`, `editable_hint: true`.
- **위젯 표 필드**: `id, resource_id, class, text, content_desc, label, bounds[l,t,r,b], clickable, editable, scrollable, action_types`. 노드당 최대 80, 인터랙티브+라벨 > 인터랙티브 > 정보 텍스트 순.
- **엣지 셀렉터**: 탐색 이벤트 `"전체보기@[1151,837][1370,1005]"` → `selector: {by, resource_id?, content_desc?, text, class, bounds, widget_id}`. 우선순위 rid > desc > text > bounds. 아이콘 탭("ImageView@…")도 출발 노드 위젯 표에서 desc 를 찾아 붙인다.
- **화면 서명**: WebView 는 리스트 행 수에 따라 `structure_str` 이 달라져 상태가 폭증한다(탐색 922 상태 → 87 화면). 현재는 pHash+제목 합침(coalesce)이 흡수. 다음 단계(계획 #1의 행 템플릿)에서 "반복 형제 그룹을 1개로 접은 구조 서명"을 추가하면 더 안정적이다.

### 2-3. 에이전트 런타임
- 셀렉터 적용 순서: `resource_id` → `content_desc` → `text`(라벨 귀속된 텍스트 = 실제로는 자식 TextView 이므로 **텍스트를 찾은 뒤 클릭 가능한 조상**을 탭) → `bounds`(해상도 보정: 지도의 화면 크기 대비 비율).
- 검색: `editable_hint` 위젯은 탭 → 포커스 확인(`EditText` 등장) → 유니코드 입력 → Enter/제출 아이콘.
- 현재 위치 파악: WebView 는 액티비티가 늘 같으므로 `activity` 로는 못 가른다 → 제목 텍스트 + 구조 서명 + pHash 로 노드 매칭(계획 #5 `locate`).
- 외부 사이트 노드(`is_external`)는 목적지에서 제외.

## 3. 대안 검토
| 방법 | 장점 | 한계 | 판단 |
|---|---|---|---|
| Chrome DevTools Protocol (WebView 원격 디버깅) | DOM·URL·id 전부 → 완벽한 grounding | `setWebContentsDebuggingEnabled(true)` 가 켜진 debuggable 빌드에서만 가능. 스토어 릴리스 앱은 대부분 꺼짐 | 디버그 빌드 한정 옵션 (미구현) |
| OCR / 비전 모델로 요소 찾기 | 접근성 트리가 비어 있어도 동작 | 느림(요소당 1~2초), 좌표 의존 | 아이콘 전용 버튼(desc 없음)의 이름 짓기용 보조 |
| 접근성 트리 + 라벨 귀속 (**채택**) | 앱 수정 불필요, 텍스트·desc 로 안정적 셀렉터 | resource-id 없음, 리스트 행 반복 | 기본 경로 |

## 4. 남은 일 (계획 문서와 연결)
- 계획 #1: 구현·실기기 검증됨 (`docs/agent_readiness_plan.md` #1). 남은 것: 행 템플릿(같은 구조의 행 묶기), 돋보기 버튼만 있는 폼의 제출.
- 웹 div 모달 감지: id 신호가 없으므로 "화면 하단/중앙의 카드 + 확인/닫기 버튼 + 배경 뷰가 그대로" 패턴을 텍스트·기하로 잡아야 한다 (계획 #3 `blocks_parent`).
- 계획 #3: 외부 사이트 WebView `is_external`, 로그인 필요 표시.
- 계획 #5: `locate` 가 WebView 노드를 제목+서명+pHash 로 맞추는지 검증.
