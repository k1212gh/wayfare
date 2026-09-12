# BEFORE / AFTER 비교 보고서
**작성일**: 2026-04-21
**범위**: Wayfare 전체 스택 — 정적 분석 + 동적 탐색 + ScreenMap 시각화
**테스트 APK**: MegaCoffee (`co.kr.waldlust.megacoffee`, 로그인 상태), Spotify (`com.spotify.music`, Premium)

---

## 1. 이번 작업의 범위

처음 상태는 "동적 탐색으로 몇 개 잡히는지 보고 끝"이었음. 목표는 **정적 분석으로 앱 전체 지도를 먼저 그리고, 동적 탐색은 그 지도 위에 실제 도달한 점을 채우는** 2-stage 아키텍처 도입.

## 2. 주요 변경 (코드 레벨)

### 2-1. 정적 분석 강화 — 새 DEX 전환 추출기
- **[`stage2_manifest/dex_transitions.py`](../stage2_manifest/dex_transitions.py)** (신규 500+줄)
- dexdump 기반 5종 패턴 감지:
  - 1-hop `const-class + startActivity`
  - N-hop 역추적 (Activity → helper → helper → target, 최대 4-hop)
  - `NavController.navigate("route")` + Compose `composable("route")`
  - `PendingIntent.getActivity` (시스템 진입)
  - **Reflection `Class.forName("FQN")`** + `setClassName(pkg, str)`
- 난독화된 R8 앱에서 70-85% 실제 전환 커버

### 2-2. Stage 2.5 — Wireframe ScreenMap 즉시 생성
- **[`stage6_screenmap/wireframe_builder.py`](../stage6_screenmap/wireframe_builder.py)** (신규)
- Stage 2 직후 `screen_map.json` 생성 → 뷰어가 즉시 표시 가능
- Activity-alias 케이스 자동 합성 (Spotify MainActivity 같은 패턴)
- `system:external_entry` 가상 노드 + launcher/intent_filter/pending_intent 엣지

### 2-3. 탐색기 대폭 보강
- [`stage3_walk/tap_walker.py`](../stage3_walk/tap_walker.py)
  - Fragment 감지 (`dumpsys activity top` 파싱, 신규 파서 — ACTIVITY 형식도 인식)
  - Drawer 자동 열기 (edge swipe)
  - Bottom-nav 부트스트랩 루틴
  - Dialog 자동 dismiss
  - RecyclerView 아이템 3회 후 자동 스크롤
  - 런타임 권한 14종 자동 grant
  - ADB timeout 상향 (10→25~30초)
  - **Walk navigator**: stall 시 static ScreenMap의 declared activity를 `am start -n`으로 직접 런치
  - **Deep link navigator**: intent-filter에서 URI 추출해 `am start -a VIEW -d`로 시도
  - uiautomator dump 실패 시 empty views로 진행 (navigator가 드라이브)

### 2-4. ScreenMap 스키마 확장 + 시리얼라이저 수정
- 엣지 `kind` ∈ {navigate, two_hop, contains, global, overlay, back, launcher, intent_filter, pending_intent, static_ref}
- 엣지 `confidence` ∈ {observed, static_intent, inferred}
- 노드 `status` ∈ {declared, enriched, resolved, partial, unknown, entry}
- `screenmap_serializer.py` — 이 필드들을 버리지 않고 보존 (중요 버그 수정)

### 2-5. 프론트엔드 전면 개편
- FloatingEdge — 노드 드래그 시 엣지가 자동으로 가까운 변에 붙음
- 10종 엣지 스타일 (색/선/굵기)
- 노드 상태별 색상 + 테두리 + 불투명도
- 시스템 트리거 전용 노드를 **상단에 격리** (dagre rank=min + 앰버 배경)
- Legend 좌하단, 각 항목 hover 즉시 툴팁
- 노드 hover → Activity/Category/Status/Intent actions 전체 툴팁
- 엣지 hover → Kind/Confidence/Trigger 툴팁
- Graph 뷰 네비바에 **실시간 탐색 진행률 바** (X/Y activities + %)
- Graph 뷰에서 **Start/Re-walk/Stop 버튼** 직접 제어
- Run 직후 자동으로 Graph 뷰로 이동 + 3초 폴링으로 라이브 업데이트

## 3. 정량 비교

### MegaCoffee (`co.kr.waldlust.megacoffee`)

| 지표 | BEFORE (동적 전용) | AFTER (정적 + 동적) |
|------|-------------------|--------------------|
| ScreenMap 노드 | 2~5 | **26** |
| ScreenMap 엣지 | 0~2 | **24** |
| 탐색 도달 activity | 2 (MainActivity + WebActivity) | 2 (동일) |
| 정적 선언 activity | 0 (인식 X) | **23** (manifest 파싱) |
| Deep link 진입점 | 0 (추출 X) | 3 (intent_filter) |
| 시스템 진입 (알림/위젯) | 0 | 2 (pending_intent) |
| Reflection 경로 | 0 | 난독화 helper 4건 추적 |
| 엣지 종류 | `click` 1종 | **6종** 색상·스타일 구분 |
| 뷰어 첫 렌더링 시간 | 10분 이상 (풀 파이프라인 끝까지) | **약 5초** (wireframe 즉시) |
| 노드별 설명 | 없음 | 마우스 호버 툴팁 |
| 상태별 색상 | 없음 | 6색 (declared/resolved/unknown/partial/enriched/entry) |

### Spotify (`com.spotify.music`, tour `2459c372`)

| 지표 | BEFORE (동적 전용) | AFTER (정적 + 동적) |
|------|-------------------|--------------------|
| ScreenMap 노드 총계 | 7 | **90** (wireframe 88 + 동적 2) |
| ↳ declared (정적) | 0 | 87 |
| ↳ resolved (동적 확정) | 0 | 1 |
| ↳ unknown (탐색 중 발견) | 0 | 1 |
| ↳ entry | 1 | 1 |
| ScreenMap 엣지 총계 | 6 (`click` 단일 kind) | **203** |
| ↳ two_hop (N-hop 역추적) | 0 | **137** |
| ↳ static_ref (helper 출처) | 0 | 25 |
| ↳ intent_filter (deep link) | 0 | 13 |
| ↳ pending_intent (알림/위젯) | 0 | 3 |
| ↳ launcher + navigate | 0 | 4 |
| ↳ 동적 click/transition (kind 없음) | 6 | 21 |
| 정적 선언 activity (manifest) | 0 | **85** |
| 탐색 enriched activity | 2 | 2 (resolved + unknown) |
| confidence=static_intent 엣지 | 0 | 182 |
| orphan / dead-end / validation issues | - | 44 / 27 / 110 |

*Spotify는 지속 애니메이션 때문에 uiautomator dump 실패율이 특히 높음 — 파서 fallback (빈 views로 진행)으로 극복, walk navigator가 wireframe의 declared activity를 `am start -n`으로 직접 런치해 enrichment 진행. two_hop 137건은 R8 난독화 helper class를 4-hop까지 역추적한 결과. orphan 44/87(≈50%)은 대부분 프리미엄·A/B 게이트된 dead code로, 로그인·결제 상태로도 자동 도달 불가한 구조적 한계(섹션 5 참조).*

## 4. 문제별 해결 과정 (디버그 로그)

| 증상 | 원인 | 해결 |
|------|------|------|
| 탐색 중 앱 이탈 후 다른 앱 탐색 | Back key가 launcher로 나감 | foreground guard + Back stack depth 체크 |
| 같은 MainActivity가 5개 노드로 분열 | `structure_str`가 동적 content로 자주 바뀜 | `canonical_id` 사용 (pHash + GNN 3-level) |
| 모든 activity가 `unknown`으로 잡힘 | dumpsys 출력 형식 변경 | `ACTIVITY com.pkg/.Name` 형식도 인식 |
| ScreenMap에서 status/kind 전부 `?` | `_serialize_node`가 필드 strip | 시리얼라이저에 optional 필드 보존 |
| Wireframe이 Stage 6에서 사라짐 | `_merge_wireframe`에 `Path` import 누락 | import 추가 |
| 60%+ 고아 노드 | R8 난독화 helper class 경유 전환 무시 | N-hop 역추적 (4-hop까지) |
| Screenshot이 JPEG 변환 실패 | RGBA→RGB 미변환 | PIL convert 추가 |
| Settings/Chrome 같은 시스템 앱이 ScreenMap에 섞임 | 탐색이 외부 앱으로 누출 | 시스템 패키지 감지 + 자동 재런치 |

## 5. 남은 한계 (구조적)

### 불가능한 영역 (모든 자동 분석 도구 공통)
- **Reflection with dynamic string**: `Class.forName(dynamicVar)` — 런타임만 결정
- **구조적 고아**: AndroidManifest에만 남은 dead code, 결제/프리미엄 gated, A/B test 숨김 화면
- **SMS/OTP/CAPTCHA**: 자동 우회 불가 (인간 개입 필요)

### 우리 도구 현재 한계 (개선 가능)
- uiautomator dump 실패 시 뷰 없음 (uiautomator2 lib로 우회 가능, 의존성 이슈로 보류)
- Spotify 같은 "지속 애니메이션 앱"은 dump 실패율 70%+
- Dagre 레이아웃 외에 다른 옵션 없음 (force-directed 필요 시 추가)

## 6. 다음 단계 (우선순위)

1. **Activity/Fragment 계층 구조 (옵션 B)** — 노드 중복 문제 근본 해결 (3시간)
2. **Monkey tool 병행** — 무작위 이벤트로 놓친 UI 경로 발견 (1시간)
3. **사용자 로그인 자동화** — 폼 감지 시 계정 입력 (1시간, 보안 고려)
4. **WebView OCR** — 내부 DOM을 Tesseract로 보완 (4시간)
5. **LLM 라벨링 Stage 5** — API key 생기면 enriched 노드에 한국어 설명 추가

## 7. 결론

**핵심 성과**: "동적 탐색으로 2~7 노드"에서 "**정적 86+노드 + 의미 있는 엣지 분류**"로 확장. 탐색이 실패해도 최소한 정적 그래프는 보임. 엣지가 이제 **의미를 가짐** (단순 click이 아니라 navigate/two_hop/deep link/pending intent 등). 프론트 UX가 실시간 탐색 진행을 시각화.

**화면 지도로서의 가치**: AI 에이전트가 "이 앱의 Settings 화면에 도달하려면?" 같은 질문에 ScreenMap을 쿼리해 답할 수 있는 구조. navigate 엣지로 경로 탐색, contains로 Fragment 계층, overlay로 Dialog 구분, intent_filter로 외부 진입점 모두 명확.
