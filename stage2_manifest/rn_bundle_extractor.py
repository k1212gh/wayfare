"""React Native JS 번들에서 화면(route) 이름 추출 — A 옵션 (2026-04-24).

Manifest 만 보면 RN 앱은 9~10 activity 만 보임 (대부분 MainActivity 1개).
실제 화면들은 React Navigation routes 로 JS 번들 안에만 존재. 이걸 정적 추출해
Stage 6 의 wireframe ScreenMap 에 추가 노드로 등록한다.

대상 번들 우선순위:
  1) assets/dist/bundle.js          (webpack 평문 — 가장 잘 파싱됨)
  2) assets/index.android.bundle    (Hermes binary 또는 JSC 텍스트)
  3) assets/index.android.bundle.hbc (Hermes only)

Hermes 바이너리는 strings-스타일 추출로 fall-back. 그래도 못 잡으면 빈 리스트.
False-positive 방지: route 이름은 PascalCase/camelCase 영문 + 길이 3~40 자만.
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


# ─── 정규식 패턴 ──────────────────────────────────────────

# 1. React Navigation v5+: { name: "X", component: ... }
_PAT_NAME_COMPONENT = re.compile(
    r'name\s*:\s*["\']([A-Za-z][\w\-]{2,40})["\']\s*,\s*component\s*:',
)

# 2. JSX: <Stack.Screen name="X" /> 또는 <RootStack.Screen name="X">
_PAT_JSX_SCREEN = re.compile(
    r'<\w*Screen\s+[^>]*name\s*=\s*["\']([A-Za-z][\w\-]{2,40})["\']',
)

# 3. Object literal: routes: { Home: ..., Settings: ... } — 매우 약한 신호라 사용 안 함

# 4. SCREEN 상수 매핑: SCREEN_HOME = "Home"
_PAT_SCREEN_CONST = re.compile(
    r'\bSCREEN[_\w]*\s*[:=]\s*["\']([A-Za-z][\w\-]{2,40})["\']',
)

# 5. Mattermost / 일반 RN 앱에서 흔한 패턴: navigate("X")
_PAT_NAVIGATE_CALL = re.compile(
    r'\bnavigate\s*\(\s*["\']([A-Za-z][\w\-]{2,40})["\']\s*[\),]',
)

# 라우트 이름으로 부적격한 흔한 토큰 — false positive 제거.
# RN 라이브러리 컴포넌트들도 blocklist (Hermes binary 추출 시 노이즈).
_BLOCKLIST = {
    "true", "false", "null", "undefined", "default", "function", "return",
    "string", "number", "object", "array", "boolean",
    "props", "state", "render", "constructor",
    "Component", "PureComponent", "Fragment",
    "View", "Text", "Image", "Button",  # RN 기본 컴포넌트 (route 이름 아님)
    "Provider", "Consumer", "Context",
    "android", "ios", "web", "darwin",
    "test", "Test", "tests",
    "main", "Main", "MAIN", "index", "Index",
    # RN 라이브러리 컴포넌트 (route 아님)
    "FlatList", "VirtualizedList", "RecyclerListView", "SectionList",
    "ScrollView", "KeyboardAvoidingView", "SafeAreaView",
    "TouchableOpacity", "TouchableHighlight", "TouchableWithoutFeedback",
    "Modal", "ActivityIndicator", "RefreshControl", "Switch",
    "BottomSheet", "BottomSheetView", "BottomSheetModal", "BottomSheetScrollView",
    "DataView", "ArrayBufferView", "InputAccessoryView", "TabView",
    "StyleSheet", "Animated", "Pressable",
    "ReactNativeWebView", "ReactFiberErrorDialog",
    # Compound list/page/view 일반 노이즈
    "FloatList", "IntList", "StringList", "NewList", "OverrideList",
    "InternalProxyList", "TrustedProxyList", "InventoryList",
    "CommandList", "LinkList", "DeleteSubList", "SplitList",
    "RegView", "UninstPage", "PassPhraseDialog", "StickyContainer",
    "FlagLinRangetCommandForm", "IndianapolisActiveTab",
}

# Hermes binary 에서 라우트 후보 추출.
# 단순 단어("Message"/"Login") 매칭은 false positive 폭발 → 접미사 필수.
# PascalCase + screen-suffix 조합만 허용 (예: LoginScreen, ChannelList,
# MessageThreadModal). 최소 2 단어 합성으로 일반 단어 노이즈 차단.
_HERMES_ROUTE_HINTS = re.compile(
    r"\b("
    r"[A-Z][a-z]+(?:[A-Z][a-z]+)*"  # PascalCase 1+ 단어
    r"(?:Screen|Page|View|Modal|Dialog|Sheet|Picker|Stack|Tab|"
    r"Container|Wrapper|Activity|Form|Editor|Dashboard|Selector|"
    r"Navigator|Route|Detail|List)"  # 명확한 route 접미사 필수
    r")\b"
)


def extract_rn_routes(apk_path: Path) -> list[dict]:
    """APK 의 RN 번들에서 화면(route) 추출.

    Returns: [{"name": "...", "source": "bundle.js" | ...}, ...] — 중복 제거됨.
    """
    apk_path = Path(apk_path)
    if not apk_path.exists():
        return []

    bundle_text = _read_bundle(apk_path)
    if not bundle_text:
        logger.info("rn_bundle_extractor: no JS bundle found in %s", apk_path.name)
        return []

    routes: dict[str, str] = {}  # name → first-seen pattern
    for pat, label in (
        (_PAT_NAME_COMPONENT, "name+component"),
        (_PAT_JSX_SCREEN, "JSX <Screen>"),
        (_PAT_SCREEN_CONST, "SCREEN const"),
        (_PAT_NAVIGATE_CALL, "navigate()"),
    ):
        for m in pat.finditer(bundle_text):
            name = m.group(1)
            if name in _BLOCKLIST or name in routes:
                continue
            # PascalCase 또는 camelCase 만 (대문자 시작 권장)
            if not re.match(r'^[A-Za-z][A-Za-z0-9_-]{2,}$', name):
                continue
            routes[name] = label

    # Hermes binary 에서 strings_fallback 으로 추출한 평문에는 위 4 패턴이
    # 인접하지 않을 수 있음 → screen-name 의도가 보이는 단어 자체를 hint
    # 패턴으로 매칭. 흔한 접두/접미가 있는 PascalCase 단어 추출.
    if not routes:  # 위 4 패턴이 0건이면 (Hermes 류) hint 패턴으로 보강
        for m in _HERMES_ROUTE_HINTS.finditer(bundle_text):
            name = m.group(1)
            if name in _BLOCKLIST or name in routes:
                continue
            if len(name) < 4:
                continue
            routes[name] = "Hermes-hint"

    result = [{"name": n, "source_pattern": p} for n, p in routes.items()]
    logger.info("rn_bundle_extractor: %d unique routes from %s", len(result), apk_path.name)
    return result


def _read_bundle(apk_path: Path) -> str:
    """APK 안의 RN 번들 후보들을 모두 추출해 합쳐서 반환.

    하나만 보면 webpack 보조 번들(dist/bundle.js)에 멈춰서 진짜 RN 번들
    (index.android.bundle)을 놓침. 모든 후보를 합치는 편이 정확도 ↑.
    """
    candidates = [
        "assets/dist/bundle.js",
        "assets/index.android.bundle",
        "assets/index.android.bundle.hbc",
        "assets/main.jsbundle",
    ]
    chunks: list[str] = []
    try:
        with zipfile.ZipFile(apk_path) as z:
            names = set(z.namelist())
            for cand in candidates:
                if cand not in names:
                    continue
                try:
                    raw = z.read(cand)
                except Exception as e:
                    logger.debug("rn_bundle_extractor: read %s failed: %s", cand, e)
                    continue
                # Hermes 바이너리 감지 → strings fallback
                if raw[:4] in (b"\xc6\x1f\xbc\x03", b"\x03\xbc\x1f\xc6"):
                    logger.info("rn_bundle_extractor: %s is Hermes binary (%d MB) — strings-fallback",
                                cand, len(raw) // (1024 * 1024))
                    chunks.append(_strings_fallback(raw))
                    continue
                # 텍스트로 디코드
                try:
                    text = raw.decode("utf-8", errors="ignore")
                    if len(text) > 1000:
                        logger.info("rn_bundle_extractor: parsing %s (%d bytes plain)", cand, len(raw))
                        chunks.append(text)
                except Exception:
                    pass
    except zipfile.BadZipFile:
        logger.warning("rn_bundle_extractor: %s is not a valid APK/ZIP", apk_path)
    return "\n".join(chunks)


def _strings_fallback(raw: bytes) -> str:
    """Hermes binary 에서 ASCII 문자열만 추출 — 정확도는 낮지만 일부 라우트 잡힘."""
    out = []
    cur = bytearray()
    for b in raw:
        # printable ASCII (32~126) + 일반 control char 제외
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= 4:
                out.append(cur.decode("ascii"))
            cur = bytearray()
    if len(cur) >= 4:
        out.append(cur.decode("ascii"))
    return "\n".join(out)
