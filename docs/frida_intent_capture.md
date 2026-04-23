# Frida Intent 캡처 — 설계문

**상태**: Design (미구현)
**작성**: 2026-04-23
**목적**: gated onboarding/wizard activity 의 실제 호출 Intent를 런타임에 기록해 scan replay 와 난독화 체인 분석 동시 해소

---

## 배경

### 해결하려는 두 가지 문제

1. **Self-finishing gated activity** (Bedtime onboarding 등)
   - `am start -n pkg/.BedtimeMorningOnboardingActivity` 으로 직접 launch 시 onCreate 에서 바로 `finish()` → UI 밀리초만 유지
   - 실제 사용자 flow 에서는 `DeskClock → Bedtime 탭 → Get started` 를 거치면서 특정 Intent extras 가 전달됨
   - 현재 multi-polling 으로도 잡기 어려움 (redirect 속도가 빠름)

2. **R8 난독화 헬퍼 체인**
   - DEX 분석 결과: `from=DeskClock trigger=startActivity [hop=2] (DeskClock → bvu → bcy)`
   - `bvu`, `bcy` 는 난독화된 헬퍼 클래스. 어떤 파라미터로 target Activity 를 부르는지 static 분석으로 정확히 못 뽑음
   - 에이전트가 이 activity 로 가는 legal path 를 구성할 수 없음

### 기존 접근 한계

| 접근 | 장점 | 단점 |
|---|---|---|
| multi-polling (구현됨) | 코드 변경 적음 | 리다이렉트가 빠르면 여전히 놓침 |
| `am start -e K V` brute force | 간단 | key 조합 폭발. 문서화 안 된 key는 짐작 불가 |
| jadx 수동 디컴파일 | 정확 | 자동화 안 됨. 앱마다 수작업 |
| **Frida hook (이 문서)** | 자동 + 정확 + 재사용 | frida-server 의존성, root/debuggable 필요 |

---

## 접근법

### 핵심 아이디어

사용자가 **한 번만 수동으로 flow 를 타면**, 그동안 발생한 모든 Intent 를 Frida hook 이 기록.
- `Context.startActivity(Intent)`
- `Activity.startActivityForResult(Intent, int)`
- `Context.startService(Intent)` (옵션)
- `PendingIntent.getActivity(...)` (옵션)

각 호출 시점에 Intent 의 모든 필드 덤프:
- `getComponent()` → ComponentName (pkg + 클래스)
- `getAction()` → 문자열
- `getData()` → URI
- `getCategories()` → set
- `getExtras()` → Bundle 내용 (key → value)
- `getFlags()` → int

결과를 `workspace/{tour_id}/intents.jsonl` 로 저장.

### 재사용 방식

1. **Scan replay**: scan phase 에서 target activity 로 갈 때 먼저 intents.jsonl 에서 해당 activity 향하는 Intent 찾기 → `am start -n pkg/cls --es K V --ez K V ...` 로 재구성 → 정상적 진입. Bedtime 같은 gated activity 도 UI 캡처 성공.

2. **난독화 체인 해소**: intents.jsonl 에 `{from: "DeskClock", to: "BedtimeMorningOnboardingActivity", extras: {phase: "morning"}}` 같은 실제 관계가 기록됨. Static 분석의 `[hop=2] (bvu → bcy)` 같은 난독화 이름을 덮어써 ScreenMap 의 edge 품질 상승.

3. **Global Intent library**: 여러 앱에서 모인 intents.jsonl 을 모으면 "앱마다 어떤 required extras 패턴이 있는지" 통계 확보 가능.

---

## 아키텍처

```
              ┌───────────────────────────────────┐
              │ Android device / emulator         │
              │                                   │
              │ ┌───────────────────────────────┐ │
              │ │ frida-server (root)           │ │
              │ └───────────┬───────────────────┘ │
              │             │ attaches            │
              │             ▼                     │
              │ ┌───────────────────────────────┐ │
              │ │ target app (DeskClock)        │ │
              │ │  + injected agent.js          │ │
              │ │    - Context.startActivity    │ │
              │ │    - Intent.* getters         │ │
              │ └───────────┬───────────────────┘ │
              └─────────────┼─────────────────────┘
                            │ emits events
                            ▼
              ┌───────────────────────────────────┐
              │ Python host                        │
              │                                    │
              │  stage3_walk/                  │
              │    frida_capture.py               │
              │    ├─ connect device              │
              │    ├─ load agent.js               │
              │    ├─ on message: append jsonl    │
              │    └─ workspace/{id}/intents.jsonl│
              └───────────────────────────────────┘
```

### 파일 구성 (구현 시)

```
stage3_walk/
  frida_capture.py         # Python: device 연결 + 메시지 수신 + JSONL write
  frida_agent.js           # JS: Intent 후킹 + 필드 덤프
```

### 의존성

```
pip install frida frida-tools   # ~60 MB, frida-server 바이너리 포함
```

디바이스 쪽:
- Android emulator: 대부분 debuggable, Magisk 없이도 frida-server 푸시 가능
- 실기기: root 필요 or `gadget` 방식(APK 리패키징 — 복잡)

### `frida_agent.js` 뼈대

```javascript
Java.perform(() => {
  const Intent = Java.use("android.content.Intent");
  const Context = Java.use("android.content.Context");
  const Activity = Java.use("android.app.Activity");

  const dumpIntent = (intent) => {
    try {
      const comp = intent.getComponent();
      const extras = intent.getExtras();
      const keys = extras ? extras.keySet() : null;
      const extrasObj = {};
      if (keys) {
        const it = keys.iterator();
        while (it.hasNext()) {
          const k = it.next();
          const v = extras.get(k);
          extrasObj[String(k)] = v !== null ? String(v) : null;
        }
      }
      return {
        component: comp ? String(comp.flattenToShortString()) : null,
        action: String(intent.getAction() || ""),
        data: String(intent.getDataString() || ""),
        categories: intent.getCategories() ? Array.from(intent.getCategories().toArray()).map(String) : [],
        flags: intent.getFlags(),
        extras: extrasObj,
      };
    } catch (e) {
      return { error: String(e) };
    }
  };

  // Hook Context.startActivity(Intent)
  Context.startActivity.overload("android.content.Intent").implementation = function (intent) {
    send({ kind: "startActivity", intent: dumpIntent(intent), caller: Java.use("java.lang.Thread").currentThread().getStackTrace().slice(0, 5).map(String) });
    return this.startActivity(intent);
  };

  // Hook Activity.startActivityForResult
  Activity.startActivityForResult.overload("android.content.Intent", "int").implementation = function (intent, code) {
    send({ kind: "startActivityForResult", requestCode: code, intent: dumpIntent(intent) });
    return this.startActivityForResult(intent, code);
  };

  // (옵션) PendingIntent.getActivity
  // (옵션) Context.startService
});
```

### `frida_capture.py` 뼈대

```python
"""Frida-based Intent capture — attaches to target app and records every
Intent flowing through Context.startActivity / Activity.startActivityForResult
to workspace/{tour_id}/intents.jsonl for later scan replay."""
from __future__ import annotations
import json
from pathlib import Path

try:
    import frida  # type: ignore
except ImportError:
    frida = None


def capture_intents(
    device_serial: str, package: str, output_path: Path, duration: float = 120.0,
) -> int:
    if frida is None:
        raise RuntimeError("Install `frida` + `frida-tools` pip packages first")
    device = frida.get_device(device_serial)
    pid = device.spawn([package])
    session = device.attach(pid)
    agent_js = (Path(__file__).parent / "frida_agent.js").read_text(encoding="utf-8")
    script = session.create_script(agent_js)

    count = 0
    def on_message(msg, data):
        nonlocal count
        if msg.get("type") != "send":
            return
        payload = msg.get("payload") or {}
        with output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        count += 1

    script.on("message", on_message)
    script.load()
    device.resume(pid)

    import time
    time.sleep(duration)
    session.detach()
    return count
```

---

## 파이프라인 통합

### 통합 지점

**Option A: Stage 2.5 (사용자 수동 탐색 세션)**
- stage 2 (static) 완료 후 파이프라인이 일시정지
- 사용자에게 "지금 수동으로 앱을 돌며 들어가고 싶은 화면 모두 방문해주세요 (2~3분)" 안내
- Frida capture가 백그라운드로 Intent 전부 기록
- 사용자가 "완료" 버튼 누르면 intents.jsonl 저장
- stage 3 이 이걸 읽어 scan replay 시 활용

**Option B: Stage 3 보조 (자동)**
- Stage 3 와 동시에 Frida 가 돌면서 TapWalker 의 자동 탐색에서도 Intent 기록
- 자동 탐색이 도달한 activity 들에 대해선 auto-replay 가능
- 도달 못 한 것들은 여전히 miss — 사용자 개입 없이 완전 자동만 원하면 이 쪽

**권장 — A + B 병행**:
- 기본: B (자동)
- 사용자가 원하면: Dashboard 에 "Manual Capture Session" 버튼 → A 모드 진입 → 30-120s 기록 → 종료 후 resume

### UI 변경

Dashboard `TourCard` 에 optional 버튼:
- `[Capture Intents]` — 사용자가 직접 flow 를 타면서 Intent 수집 (Stage 2 → 3 사이 또는 run 시작 전)

---

## 위험 / 완화

| 위험 | 영향 | 완화 |
|---|---|---|
| frida-server 설치 실패 (실기기, 비-root) | Frida 전체 불가 | Emulator 는 기본 설치. 실기기는 stage3_walk/frida_capture.py 가 설치 실패 시 조용히 disable |
| Hook 메소드 overload 불일치 (Android 버전 차이) | JS 에러 | `try/catch` + `overloads` 배열 순회 |
| 광범위한 hook 이 앱 성능 저하 | 탐색 속도 ↓ | Intent 관련 메소드만 후킹 (fine-grained) |
| extras 에 Parcelable 객체 | `String()` 변환 실패 | fallback 으로 클래스명만 기록 |
| 재패키징 detection (일부 앱) | SafetyNet 차단 등 | Gadget 대신 root + frida-server 경로 (attach 방식은 감지 회피에 유리) |

---

## 예상 효과

DeskClock 기준 (현재 상태 → Frida 적용 후 추정):

| 지표 | 현재 | Frida 적용 후 |
|---|---|---|
| `A` capture rate | 25% (7/27) | **65-80%** (onboarding/wizard 복구) |
| self-finishing 활동의 UI 확보 | 불가 | 가능 (replay) |
| edge trigger_widget 품질 | `intent [hop=2] (bvu → bcy)` | `intent + extras {phase: "morning"}` |
| 난독화 체인 해소 | 정적 hop 만 | 실제 런타임 콜 체인 |

---

## 구현 공수 추정

- `frida_agent.js` 기본 훅 4개: **1시간**
- `frida_capture.py` 래퍼 + on-message 처리: **1시간**
- 파이프라인 통합 (stage3 pre-hook): **2시간**
- 에러 처리 + emulator 부재 fallback: **1시간**
- 테스트 (mock frida session): **2시간**

**총 ~1일** (7시간). Bedtime 같은 문제를 구조적으로 해결하는 효과 대비 투자 적정.

---

## 다음 단계 (언제 구현할지)

**권장**: 다음 주 우선순위 1 (Fragment 감지) + 2 (Scan 타이밍) 까지 끝낸 직후.
그 전에는:
- multi-polling + static-guided priority 조합으로 capture rate 40% 전후 도달 목표
- 이 수준에서도 놓치는 gated activity (Bedtime 류) → Frida 로 보완

---

## 참고

- Frida docs: https://frida.re/docs/javascript-api/
- Intent API: https://developer.android.com/reference/android/content/Intent
- 유사 도구 (학습용):
  - `medusa` — Frida 기반 Intent dumper
  - `objection` — Frida 래퍼, intent 명령 내장
