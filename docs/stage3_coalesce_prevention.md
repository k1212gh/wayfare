# Stage 3 중복 탐지/방지 정리

이 문서는 ScreenAtlas의 **Stage 3 동적 탐색 단계**에 적용된 중복 탐지 및 중복 방문 방지 로직만 정리한다. Stage 6의 ScreenMap 노드 병합(`semantic_merge`, Vision LLM coalesce)은 별도 문서에서 다룬다.

## 목적

Stage 3의 목표는 앱을 탐색하면서 같은 논리 화면을 여러 번 새 화면으로 기록하지 않는 것이다. 동시에 서로 다른 화면을 성급하게 합치지 않아야 한다.

주요 문제는 다음과 같다.

- 시계, 타이머, 상태바처럼 매 캡처마다 값이 바뀌는 UI 때문에 같은 화면이 여러 `state_str`/`structure_str`로 쪼개짐
- RecyclerView, LazyColumn, WebView처럼 자식 view 수가 흔들리는 화면에서 스크롤 위치마다 새 canonical이 생김
- Compose/WebView/Flutter 계열처럼 accessibility 정보가 적은 화면에서 서로 다른 화면이 같은 구조로 보임
- 탐색 루프가 같은 canonical 또는 두 canonical 사이를 반복하며 이벤트 예산을 낭비함

## 전체 흐름

Stage 3의 중복 방지는 네 단계로 작동한다.

1. `CaptureMixin._capture_screen()`가 UI XML과 screenshot을 캡처한다.
2. `signature_stabilizer`가 동적 노이즈를 제거한 `structure_str`, `state_str`를 만든다.
3. `ScreenSigner`가 `ScreenSignature`를 만들고 기존 fingerprint와 비교해 `canonical_id`를 결정한다.
4. `TapWalker`가 `canonical_id` 기준으로 방문 횟수, 시도한 액션, stall/ping-pong 상태를 추적한다.

관련 코드:

- `stage3_walk/mixins/capture.py`
- `stage3_walk/signature_stabilizer.py`
- `stage3_walk/screen_signer.py`
- `stage3_walk/tap_walker.py`
- `stage3_walk/list_view_detector.py`
- `stage3_walk/view_tree_readers/xml_view_tree.py`

## 1. 캡처 시점 해시 안정화

`signature_stabilizer.py`는 탐색 중 발생하는 false split을 줄이는 1차 방어선이다.

### 동적 class 제거

다음 class는 구조 해시에서 제외한다.

- `android.widget.AnalogClock`
- `android.widget.TextClock`
- `android.widget.Chronometer`
- `android.widget.ProgressBar`
- `android.media.AudioVisualization`
- `androidx.core.widget.ContentLoadingProgressBar`

이들은 시간, 진행률, 애니메이션 때문에 값이 계속 변하지만 논리 화면을 구분하는 근거로는 약하다.

### resource-id 숫자 suffix 안정화

`timer_item_3`, `alarm_row_12`, `id:0002`처럼 끝의 숫자만 다른 id는 같은 패턴으로 본다.

예:

```text
timer_item_3  -> timer_item_*
alarm_row_12  -> alarm_row_*
id:0002       -> id:*
```

목적은 리스트 항목 index 차이 때문에 같은 화면이 여러 canonical로 쪼개지는 것을 막는 것이다.

### 시간성 content-desc 마스킹

`3:45`, `12:05:30`, 날짜, 초 단위 숫자 등은 `*`로 마스킹한다. 시계/타이머 앱에서 초 단위 변화가 `structure_str`를 바꾸는 문제를 막는다.

### scrollable container 자식 collapse

`collapse_scroll_children()`은 scrollable parent는 유지하되 직접 자식 view는 해시 입력에서 제거한다. Feed/List 화면에서 스크롤 위치나 lazy inflate 상태에 따라 child count가 바뀌어도 같은 논리 화면으로 유지하기 위한 처리다.

## 2. 3-Level Screen Signature

`ScreenSigner`는 `ScreenSignature`를 만든다.

```python
ScreenSignature(
    structural_hash,
    perceptual_hash,
    gnn_embedding,
    screenshot_md5,
    activity,
    widget_count,
)
```

### L1: structural hash

Stage 3의 핵심 판정 기준이다. `screen_signer.py`의 structural hash는 다음 신호를 사용한다.

- activity
- view class와 clickable/scrollable/editable flag
- stable resource-id, stable content-desc
- accessibility 정보가 부족한 화면에서는 clickable view의 Y-bucket layout signature

중요한 정책은 **L1 authoritative**다.

양쪽 fingerprint에 `structural_hash`가 있으면:

- `structural_hash`가 같음 -> 같은 화면
- `structural_hash`가 다름 -> 다른 화면
- 이 경우 pHash/GNN은 L1 판단을 뒤집지 못함

이 정책은 DeskClock처럼 다른 탭인데 GNN fallback feature가 비슷해서 한 화면으로 과병합되던 문제를 막는다.

### L2: perceptual hash

pHash는 screenshot 기반 시각 유사도다. 상태바와 내비게이션바 노이즈를 줄이기 위해 screenshot의 상단 5%, 하단 8%를 crop한 뒤 계산한다.

기본 threshold:

```text
PHASH_DIST_THRESHOLD=10
```

단, 현재 Stage 3 matching에서는 L1이 양쪽에 있으면 pHash가 L1을 override하지 못한다. pHash는 L1이 비어 있거나 신뢰하기 어려운 Canvas 계열 화면의 fallback 성격이다.

### L3: GNN/fallback embedding

UI tree를 feature vector로 만든 뒤 cosine similarity로 비교한다. `torch_geometric`이 없으면 count 기반 fallback vector를 사용한다.

기본 threshold:

```text
GNN_SIM_THRESHOLD=0.82
```

이 역시 L1이 양쪽에 있으면 L1을 override하지 못한다. 과거 `0.95` 기준에서도 DeskClock 탭들이 거의 같은 vector로 보이는 문제가 있었기 때문에, 현재는 L1 gate가 더 중요한 안전장치다.

## 3. WebView/Compose 계열 보정

Accessibility 정보가 빈약한 화면에서는 class hierarchy만으로 여러 화면이 동일하게 보일 수 있다. 이를 보완하기 위해 `ScreenSigner._structural_hash()`는 다음 처리를 한다.

### layout fallback

조건:

```text
n_click >= 3 and n_a11y_unique < n_click
```

이 조건을 만족하면 clickable view의 Y 좌표를 50px bucket으로 양자화해 structural hash에 포함한다.

효과:

- Megacoffee Home과 MegaOrder처럼 anonymous `View` 구조는 비슷하지만 layout이 다른 화면을 구분한다.
- 1px 수준의 작은 bounds jitter는 같은 bucket에 남아 false split을 막는다.

### count jitter log bucket

WebView 캡처에서는 anonymous `View` 수가 107, 109, 110처럼 조금씩 흔들릴 수 있다. 이를 그대로 count하면 같은 화면이 여러 canonical로 쪼개진다.

그래서 `(class, flags)` count를 다음 bucket으로 양자화한다.

```text
1, 2, 3-4, 5-9, 10-19, 20-49, 50-99, 100+
```

작은 렌더링 jitter는 흡수하되, 2개짜리 sparse 화면과 50개짜리 dense 화면처럼 진짜 구조 차이는 유지한다.

### SystemUI overlay 제거

`clock`, `battery`, `wifi`, `mobile`, `status_bar` 계열 resource-id는 accessibility signal에서 제외한다. 상태바 시간, 통신사, 신호 세기가 바뀌어 같은 앱 화면의 hash가 분열되는 문제를 막는다.

## 4. canonical_id 부여와 탐색 상태 관리

`TapWalker`는 매 캡처마다 다음 과정을 수행한다.

1. `ScreenSigner.compute_fingerprint()` 호출
2. `ScreenSigner.find_match()`로 기존 canonical 탐색
3. match가 있으면 기존 `canonical_id` 재사용
4. match가 없으면 `screen_###` 새 canonical 등록
5. state에 `canonical_id`, `structure_str`, `state_str=canonical_id` 기록

이후 모든 탐색 제어는 raw state가 아니라 canonical 기준으로 움직인다.

주요 상태:

- `visited_structures[canonical_id]`: canonical 방문 횟수
- `visited_screens`: 방문한 canonical set
- `tried_actions[canonical_id]`: 이 화면에서 이미 시도한 action desc
- `hash_stats`: L1/L2/L3 match 수와 신규 화면 수

결과적으로 같은 화면을 다시 만나면 새 화면으로 보지 않고, 방문 횟수와 시도 액션 정보가 누적된다.

## 5. 중복 방문/무한 루프 방지

Stage 3는 화면 중복 판정뿐 아니라 같은 화면을 계속 누르는 탐색 낭비도 줄인다.

### tried action penalty

`xml_view_tree.score_action()`은 같은 `canonical_id`에서 이미 시도한 action에 강한 penalty를 준다. 미시도 action에는 bonus를 준다.

핵심 효과:

- 같은 화면에서 같은 버튼을 반복 클릭하지 않음
- canonical이 안정적으로 유지될수록 action coverage가 누적됨

### visit count penalty

방문 횟수가 많은 canonical에서는 action score가 낮아진다.

```text
score -= visit_count * 2.5
```

같은 화면에 오래 머무를수록 다른 경로로 빠져나가려는 압력이 커진다.

### list_view redundancy penalty

`list_view_detector.py`는 반복 sibling pattern을 list_view으로 마킹한다. 첫 항목은 탐색하지만 이후 같은 list_view group의 N번째 항목은 점점 큰 penalty를 준다.

목적:

- 메뉴/상품/이벤트 grid에서 모든 항목을 반복 클릭하지 않음
- 하나의 detail 패턴만 샘플링하고 나머지는 일반화

단, 옵션 선택 화면처럼 진짜 다른 상태가 될 수 있는 화면은 Stage 3 hash와 후속 분석으로 별도 판단한다.

### scroll budget

각 canonical별 scroll 횟수를 제한한다. 무한 스크롤 feed에서 매 swipe가 새 item을 로딩해도 탐색이 계속 scroll에 갇히지 않게 한다.

### same canonical stall escape

같은 canonical이 연속으로 반복되면 stall으로 보고:

1. 아직 안 누른 action을 강제 시도
2. 그래도 안 되면 Vision fallback 또는 Back
3. Back이 앱을 종료할 위험이 있으면 soft restart

### ping-pong loop detection

최근 canonical window에서 `A,B,A,B,A,B,A` 패턴을 감지한다. 이 경우 최근 진입 action desc를 blacklist/learned action으로 저장하고 Back 또는 restart로 loop를 끊는다.

false positive를 줄이기 위해 `A,A,A,A,B,B,B` 같은 block pattern은 ping-pong으로 보지 않는다.

## 6. Stage 4로 넘기는 보존 정보

Stage 3의 중복 방지 결과는 다음 필드로 후속 단계에 전달된다.

- `canonical_id`
- `structure_str`
- `state_str = canonical_id`
- `screenshot_md5`
- `screenshot_path`

Stage 4 `screen_clusterer.py`는 `canonical_id`를 우선 grouping key로 사용한다. 이 덕분에 Stage 3에서 이미 합쳐진 화면이 Stage 4에서 `structure_str` 기준으로 다시 쪼개지는 문제가 줄어든다.

## 7. 설정값

Stage 3 관련 환경변수:

```text
PHASH_DIST_THRESHOLD=10
GNN_SIM_THRESHOLD=0.82
```

주의:

- pHash/GNN threshold를 올려도 L1이 존재하면 L1 판단을 뒤집을 수 없다.
- 구조가 비어 있거나 빈약한 화면에서만 L2/L3 fallback 의미가 커진다.

## 8. 검증 테스트

관련 테스트:

- `tests/test_coalesce.py`
  - `signature_stabilizer` 안정화
  - dynamic class, ticking clock, RecyclerView suffix drift 방어
- `tests/test_screen_signer_layout.py`
  - anonymous WebView layout fallback
  - bounds jitter tolerance
  - SystemUI overlay 제거
  - WebView count jitter collapse
- `tests/test_pingpong_detection.py`
  - canonical ping-pong loop 탐지
  - block pattern false positive 방지

확인한 명령:

```powershell
pytest tests\test_coalesce.py tests\test_screen_signer_layout.py tests\test_pingpong_detection.py
```

결과:

```text
56 passed
```

`.pytest_cache` 쓰기 권한 warning은 있었지만 테스트 실패는 없었다.

## 요약

Stage 3의 중복 방지는 단순히 screenshot 유사도를 보는 방식이 아니다. 먼저 동적 UI 노이즈를 제거해 안정적인 구조 해시를 만들고, L1 structural hash를 authoritative하게 둔다. WebView/Compose처럼 구조 정보가 부족한 화면에는 layout fallback을 추가하되, rich accessibility 화면에는 layout 차이를 과도하게 반영하지 않는다.

이후 `canonical_id`를 탐색 제어의 기준으로 사용해 방문 횟수, 시도 액션, stall/ping-pong 상태를 누적한다. 즉 Stage 3의 핵심은 **같은 화면은 같은 canonical로 수렴시키고, 같은 canonical 안에서는 같은 행동을 반복하지 않게 만드는 것**이다.
