# XML 누락 요소 처리 정책

이 문서는 ScreenAtlas 파이프라인에서 XML 입력의 **누락 요소(missing element/attribute)** 가 발생했을 때 각 모듈이 어떻게 동작하는지 정리한다. 대상 입력은 다음 네 종류다.

- `AndroidManifest.xml` (binary 또는 decoded)
- `res/layout/*.xml` (정적 레이아웃 정의)
- uiautomator UI dump (`window_dump.xml`)
- DroidBot view 트리 (이미 dict로 변환된 상태)

## TL;DR — 누락 시 동작 요약

| 레이어 | 누락 시 동작 | 정책 |
|---|---|---|
| 파일/디렉토리 없음 | 빈 결과 + warning 로그 | fail-soft |
| XML ParseError | 해당 파일만 skip 또는 빈 결과 | fail-soft (단 모듈마다 로그 유무 다름) |
| 속성 누락 | `attrib.get(key, "")` 빈 문자열 default | silent fallback |
| 필수 컨테이너 누락 (`<application>` 등) | None 체크 → 빈 리스트 | fail-soft |
| `entry_activity` 누락 | 첫 activity로 fallback 추정 | 추정 — 부정확 가능 |

핵심 원칙은 **"파이프라인을 멈추지 않는다"** 다. 한 화면/한 layout 파싱이 실패해도 다음 단계로 넘어간다. 대신 downstream 단계가 빈 입력을 견딜 수 있어야 한다.

## 1. AndroidManifest 파서

**파일**: [screenatlas/stage2_manifest/manifest_parser.py](../stage2_manifest/manifest_parser.py)

binary APK는 androguard, decoded XML은 `xml.etree`로 처리하며 dispatch는 [L18-29](../stage2_manifest/manifest_parser.py#L18-L29) 에서 결정된다.

| 라인 | 누락 케이스 | 처리 |
|---|---|---|
| [L132](../stage2_manifest/manifest_parser.py#L132) | `package` 속성 누락 | `attrib.get("package", "")` — 빈 문자열 |
| [L136-138](../stage2_manifest/manifest_parser.py#L136-L138) | `<application>` 자체 누락 | `app is None` 체크 후 activities 빈 리스트로 진행 |
| [L141, 145-146, 156](../stage2_manifest/manifest_parser.py#L141-L156) | `android:name`, `android:exported` 등 누락 | 모두 `attrib.get(..., "")` |
| [L159-160](../stage2_manifest/manifest_parser.py#L159-L160) | `<intent-filter>` 누락 → launcher 미감지 | `entry_activity`가 비어 있으면 **첫 activity를 fallback** |
| [L177-179](../stage2_manifest/manifest_parser.py#L177-L179) | `app=None` 시 `_parse_components` | 빈 리스트 반환 |
| [L22-29](../stage2_manifest/manifest_parser.py#L22-L29) | etree 파싱 자체 실패 | `except Exception` → androguard로 재시도, 그것도 실패하면 `raise` |

**정책**: silent fallback. 단 entry_activity는 **추정값**이라 downstream에 영향이 클 수 있다.

## 2. 정적 레이아웃 분석기

**파일**: [screenatlas/stage2_manifest/layout_analyzer.py](../stage2_manifest/layout_analyzer.py)

`res/layout*/*.xml` 을 순회하며 clickable/input/scrollable 요소를 추출한다.

| 라인 | 누락 케이스 | 처리 |
|---|---|---|
| [L36-38](../stage2_manifest/layout_analyzer.py#L36-L38) | `layout/` 디렉토리 자체 없음 | warning 로그 + 빈 리스트 |
| [L51-52](../stage2_manifest/layout_analyzer.py#L51-L52) | `ET.ParseError` (malformed XML) | warning 로그 후 **해당 파일만 skip** (전체 중단 X) |
| [L68-71](../stage2_manifest/layout_analyzer.py#L68-L71) | `android:id`, `android:text` 등 누락 | `attrib.get(..., "")` |

**정책**: 파일 단위 fail-soft, 속성 단위 빈 문자열 default. 4개 모듈 중 가장 견고하다.

## 3. uiautomator UI dump 파서

**파일**: [screenatlas/stage3_walk/view_tree_parser.py](../stage3_walk/view_tree_parser.py)

탐색 중 캡처된 view hierarchy XML을 flat list[dict]로 변환한다.

| 라인 | 누락 케이스 | 처리 |
|---|---|---|
| [L30-31](../stage3_walk/view_tree_parser.py#L30-L31) | 파일 자체 없음 | `xml_path.exists()` 체크 → 빈 리스트 |
| [L32-35](../stage3_walk/view_tree_parser.py#L32-L35) | `ET.parse` 실패 | 광범위 `except Exception` → 빈 리스트, **로그 없음** |
| [L43-61](../stage3_walk/view_tree_parser.py#L43-L61) | `class`, `resource-id`, `text`, `bounds` 등 누락 | 모두 `attrs.get(..., "")` 또는 `attrs.get(...) == "true"` |
| [L311-313](../stage3_walk/view_tree_parser.py#L311-L313) | root `bounds` 파싱 실패 | `(False, None)` → webview fallback 비활성 |

**정책**: 모든 실패를 silent로 흡수. 디버깅 어려움이 단점이다 (개선 후보, §5 참고).

## 4. DroidBot view dict 정리

**파일**: [screenatlas/stage4_screens/view_tree_cleaner.py](../stage4_screens/view_tree_cleaner.py)

이 단계는 이미 dict로 변환된 view를 정리하므로 XML 파싱은 없다. 하지만 누락 처리 정책은 일관되게 적용된다.

| 라인 | 누락 케이스 | 처리 |
|---|---|---|
| [L41-42](../stage4_screens/view_tree_cleaner.py#L41-L42) | `visible` 속성 없음 | default `True` (보수적 — 안 보이는 걸 보이는 걸로 가정) |
| [L62-67](../stage4_screens/view_tree_cleaner.py#L62-L67) | 모든 의미있는 속성이 비어 있고 children도 없음 | **노드 자체 drop** |

**정책**: 의미 없는 노드는 정제 단계에서 적극 제거한다. LLM 입력 토큰을 아끼는 게 목적.

## 5. 위험 신호 / 개선 후보

현재 코드 기준으로 운영상 주의해야 할 지점.

| 우선순위 | 위치 | 문제 | 권장 수정 |
|---|---|---|---|
| **High** | [view_tree_parser.py:33-35](../stage3_walk/view_tree_parser.py#L33-L35) | ParseError와 다른 버그를 구분 못 함 | 최소 `logger.warning("ui_xml parse failed: %s", xml_path)` 추가 |
| **High** | [manifest_parser.py:24](../stage2_manifest/manifest_parser.py#L24) | text XML 실패 시 원본 에러가 사라짐 | `logger.warning("etree parse failed: %s, falling back", e)` |
| **Medium** | [manifest_parser.py:159-160](../stage2_manifest/manifest_parser.py#L159-L160) | launcher 미감지 시 첫 activity를 entry로 추정 — SplashActivity나 internal-only일 수 있음 | 빈 값이면 명시적 warning 또는 에러로 사람이 확인하게 |
| **Low** | [view_tree_cleaner.py:41](../stage4_screens/view_tree_cleaner.py#L41) | `visible` 누락 시 True default — 실제 invisible view를 보이는 것으로 잘못 분류 가능 | 현재로는 droidbot이 항상 채워주므로 후순위 |

## 6. 일관 원칙 체크리스트

새 XML 처리 코드를 추가할 때 따라야 할 규칙.

- [ ] 파일 존재 여부 먼저 확인 (`Path.exists()`)
- [ ] `ET.ParseError`를 좁게 잡고 **반드시 로그 남기기**. `except Exception`는 지양
- [ ] 속성 접근은 `attrib.get(key, default)` 패턴으로 통일 (인덱싱 `attrib[key]` 금지)
- [ ] None 컨테이너 가능성 있는 노드(`root.find(...)`)는 None 체크 후 진행
- [ ] **추정 fallback** 은 명시적으로 로그를 남기고, 가능하면 추정 여부를 반환 dict에 표기
- [ ] downstream 단계가 빈 리스트/빈 dict를 받아도 동작해야 함 (단위 테스트 필요)

## 참고

- 관련 정적 분석 단계 전체 흐름: [stage3_coalesce_prevention.md](stage3_coalesce_prevention.md)
