# 정점 탐색 강화 — BEFORE / AFTER 보고서

**일자**: 2026-04-21
**범위**: Stage 3 (동적 탐색) + Stage 4 (클러스터링) + Stage 6 (ScreenMap 빌드)
**테스트 대상**: com.spotify.music (100.3MB, 85 activities 선언)

---

## 1. 문제 진단 (BEFORE)

### 1-1. 측정 데이터 — 기존 Spotify tour `3965e8e7`

| 지표 | 값 | 문제 여부 |
|------|-----|----------|
| Stage 3 캡처 이벤트 수 | 89 | — |
| 고유 activity 수 (`dumpsys`) | **2** (MainActivity, PageActivity) | ⚠️ 활동 85개 중 2개만 보임 |
| 고유 `structure_str` | 12 | — |
| 3-Level hasher canonical_id | **4** | ⚠️ 과도 병합 또는 탐색이 얕음 |
| 관찰된 transition | 21 | — |
| Unique activity-pair transition | **1** (전부 MainActivity→MainActivity) | ❌ 근본 문제 |
| 최종 ScreenMap nodes | 5 | — |
| 최종 ScreenMap edges | 4 | — |
| Entry node에서 도달 가능 | **1** (entry 자기 자신) | ❌ **80% 고아** |

### 1-2. 왜 이런 결과가 나왔나 — 근본 원인 5가지

#### ① Fragment 미감지 → 같은 화면 취급 [🔴 critical]

**증거**: 21개 transition 전부 `MainActivity→MainActivity`. Spotify UI는 실제로 Home/Search/Library 3개 탭으로 나뉘어 있지만, 모두 MainActivity 하나 안의 Fragment 교체로 구현됨. `dumpsys activity activities`는 activity 이름만 주고 Fragment는 안 줌.

**문제 코드** [stage3_walk/tap_walker.py:454](stage3_walk/tap_walker.py#L454) (수정 전):
```python
structure_str = hashlib.sha256(
    f"{activity}|{'|'.join(clickable_ids)}".encode()
).hexdigest()
```
`activity`만 해시에 포함 → Home 탭과 Search 탭의 structure_str이 같아질 가능성.

#### ② entry_node 선택 로직 결함 [🔴 critical]

**증거**: Entry node `page_d8bb3b35b757`에서 실제로 도달 가능한 노드가 자기 자신 1개. 4개 노드가 고아. 의미: entry가 **실제 탐색 시작점과 다른 MainActivity 변형**에 배정됨 → transitions 그래프와 entry가 연결 안 됨.

**문제 코드** [stage6_screenmap/screenmap_builder.py:_find_entry_node](stage6_screenmap/screenmap_builder.py#L167) (수정 전):
```python
for sid, node in nodes.items():
    if "main" in node["activity"].lower():
        return sid  # dict 삽입 순서로 첫 번째 선택
```
Spotify 5개 MainActivity 변형 중 임의로 첫 번째 → 전이 그래프와 동떨어진 노드가 entry가 됨.

#### ③ screen_clusterer가 structure_str로만 그룹핑 [🟠 high]

**증거**: 89 captures → 12 structure_str → 4 canonical_id → 5 ScreenMap nodes. TapWalker가 이미 pHash·GNN으로 12→4 병합했는데, Stage 4는 이 결과를 버리고 structure_str로 다시 그룹핑.

**문제 코드** [stage4_screens/screen_clusterer.py:42](stage4_screens/screen_clusterer.py#L42) (수정 전):
```python
key = state.get("structure_str", "")
groups[key].append(state)
```
3-Level hasher의 업적을 폐기.

#### ④ Dialog 팝업에 갇힘 [🟠 high]

**증거**: `rv_taps_per_screen`은 기록 안 되어 있지만, 이전 Megacoffee run에서 `GrantPermissionsActivity`, `NexusLauncherActivity`가 노드로 들어왔던 사실 = 다이얼로그/런처가 탐색 상태로 누적됨.

**문제**: 우리 is_dialog 감지 / dismiss 로직 없음. 권한 팝업 뜨면 back 눌러도 다시 떠서 무한 루프.

#### ⑤ RecyclerView 아이템 루프 [🟠 high]

**증거**: 모든 transitions가 MainActivity→MainActivity (자기 자신). Spotify 홈 화면 RecyclerView에 50+ 앨범/플레이리스트 아이템이 있는데, 탐색기가 각각을 "다른 미시도 액션"으로 보고 순차 탭. 각 탭은 PageActivity 열었다가 back → MainActivity 복귀. 이벤트 낭비.

**문제**: 아이템 N개 탭 후 스크롤 전략 부재.

---

## 2. 수정 적용 (AFTER 코드)

| 문제 | 수정 위치 | 변경 |
|------|----------|------|
| ① Fragment 미감지 | [tap_walker.py:442-465](stage3_walk/tap_walker.py#L442-L465) | `dumpsys activity top` 파싱 → `_extract_fragment` → structure_str + state에 `fragment` 필드 |
| ② entry 고아 | [screenmap_builder.py:_find_entry_node](stage6_screenmap/screenmap_builder.py#L167) | screen_cards 첫 원소 우선 사용 (탐색 시작점 보장) |
| ③ clusterer 중복 | [screen_clusterer.py:42-51](stage4_screens/screen_clusterer.py#L42) | 그룹핑 키 = `canonical_id or structure_str` |
| ④ Dialog 갇힘 | [tap_walker.py:226-231](stage3_walk/tap_walker.py#L226-L231) + `_dismiss_dialog` | `is_dialog` 감지 → dismiss 버튼(닫기/취소/X) 자동 탭 |
| ⑤ RV 루프 | [tap_walker.py:324-338](stage3_walk/tap_walker.py#L324-L338) + `_scroll_down` | 화면당 RV 아이템 3회 탭 후 자동 스크롤 |

### 보조 개선

- **트랩 집계**: `self.trap_stats` dict에 `dialog_dismissed` / `rv_cap_scrolled` / `out_of_app_relaunch` 카운트 → run 종료 시 한 줄 로그
- **label_hint**: 각 페이지의 뷰에서 Toolbar/상단 TextView 텍스트 추출 → `page.label_hint` → screenmap_builder가 라벨에 `MainActivity · 추천` 식으로 결합
- **휴리스틱 분류기** [stage6_screenmap/heuristic_classifier.py](stage6_screenmap/heuristic_classifier.py): password/검색/설정 등 패턴으로 `functional_category` 자동 부여

---

## 3. AFTER 측정 결과

*(진행 중 — 에뮬 cold boot 완료 후 재실행 예정. 재실행 후 이 섹션 채워짐)*

예상 변화 (합리적 추정):

| 지표 | BEFORE | AFTER 예상 | 개선 이유 |
|------|--------|-----------|----------|
| 고유 canonical 화면 | 4 | **8~12** | Fragment 분리 |
| Activity-pair transition 종류 | 1 | **3~5** | 여러 Fragment 간 이동 기록 |
| ScreenMap orphan 비율 | 80% (4/5) | **<20%** | entry_node 수정 |
| Dialog 유령 노드 | 존재 | **0** | 자동 dismiss |
| RecyclerView 이벤트 낭비 | 심각 | 화면당 3회 cap | RV 제한 |

---

## 4. 남은 과제 (Tier 2)

| 개선 | 해결하는 함정 | 비용 |
|------|--------------|------|
| pHash threshold 완화 (동일 화면 다른 배너 병합) | 광고 배너 → 별도 노드 | 1시간 |
| content-desc "Image N of M" 쓰레기 필터 | 라벨 오염 | 15분 |
| 키보드 자동 닫기 (`input keyevent 111`) | 화면 일부 잘림 | 15분 |
| 화면 밖 요소 우선 스크롤 | 스크롤 영역 내부 탐색 | 1시간 |

### Tier 3 (큰 공사)

| 개선 | 해결하는 함정 | 비용 |
|------|--------------|------|
| WebView OCR (Tesseract) | 메가커피 같은 WebView 앱 | 4시간 |
| Flutter Semantics 활성화 | Flutter 앱 요소 0개 | 2시간 |

---

## 5. 시각화 (AFTER 측정 후 추가 예정)

- [ ] BEFORE/AFTER 노드 수 막대 차트
- [ ] 엣지 밀도 비교
- [ ] 고아 노드 비율 파이 차트
- [ ] 트랩 적중 로그 (첫 실제 run 이후 기록)
