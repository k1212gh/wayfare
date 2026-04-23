"""APK 프레임워크 자동 감지.

4가지로 분류:
- flutter:       Flutter 앱 (Canvas 렌더링, Semantics 필요)
- react-native:  React Native 앱 (JS 브리지, RCT* 클래스)
- compose:       Jetpack Compose 앱 (선언형 UI, clickable=false 문제)
- xml:           기존 XML 기반 네이티브 (Java/Kotlin)

감지 순서: flutter → react-native → compose → xml(기본값)
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Framework = Literal["xml", "compose", "flutter", "react-native"]


def detect_framework(apk_path: str | Path) -> dict:
    """APK 프레임워크 감지.

    결정 트리:
      1. flutter 마커 존재 (libflutter.so / flutter_assets/) → flutter
      2. react-native 마커 존재 (index.android.bundle / libhermes.so) → react-native
      3. compose dex 참조 + layout XML 10개 미만 → compose (UI가 완전 Compose)
      4. 그 외 → xml (전통 네이티브, Compose 라이브러리 일부 사용도 여기)

    Returns:
        {
            "framework": "xml" | "compose" | "flutter" | "react-native",
            "evidence": ["감지 근거 1", ...],
            "layout_xml_count": int,  # 참고용 메트릭
        }
    """
    apk_path = Path(apk_path)
    if not apk_path.exists():
        return {"framework": "xml", "evidence": ["APK not found, default to xml"], "layout_xml_count": 0}

    evidence: list[str] = []
    layout_count = _count_layout_xml(apk_path)
    evidence.append(f"res/layout XMLs: {layout_count}")

    # 1. Flutter 먼저 체크 (가장 강한 시그널)
    if _is_flutter(apk_path, evidence):
        return {"framework": "flutter", "evidence": evidence, "layout_xml_count": layout_count}

    # 2. React Native
    if _is_react_native(apk_path, evidence):
        return {"framework": "react-native", "evidence": evidence, "layout_xml_count": layout_count}

    # 3. Compose-heavy: layout 거의 없고 compose dex 참조 있으면
    has_compose = _is_compose(apk_path, evidence)
    if has_compose and layout_count < 10:
        evidence.append(f"compose-heavy: {layout_count} layouts (< 10 threshold)")
        return {"framework": "compose", "evidence": evidence, "layout_xml_count": layout_count}

    # 4. 기본: xml 네이티브 (Compose 라이브러리 일부 사용해도 XML이 주)
    if has_compose:
        evidence.append(f"xml with some compose libs ({layout_count} layouts)")
    else:
        evidence.append("pure xml native")
    return {"framework": "xml", "evidence": evidence, "layout_xml_count": layout_count}


def _count_layout_xml(apk_path: Path) -> int:
    """APK의 res/layout*.xml 파일 개수 — XML vs Compose 판별 시그널."""
    try:
        with zipfile.ZipFile(apk_path) as zf:
            return sum(1 for n in zf.namelist()
                       if n.startswith("res/layout") and n.endswith(".xml"))
    except zipfile.BadZipFile:
        return 0


def _is_flutter(apk_path: Path, evidence: list[str]) -> bool:
    """Flutter 앱 감지.

    dex에 'flutter' 문자열은 네이버맵 등 라이브러리가 내부에서 참조할 수 있어 신뢰 불가.
    네이티브 엔진 또는 전용 asset 경로만 확실한 시그널.

    - libflutter.so (필수 네이티브 엔진)
    - assets/flutter_assets/ (필수 Flutter 리소스 폴더)
    """
    try:
        with zipfile.ZipFile(apk_path) as zf:
            names = zf.namelist()
            for name in names:
                if "libflutter.so" in name:
                    evidence.append(f"flutter: {name}")
                    return True
                # 정확히 flutter_assets 디렉토리 구조만 인정
                if name.startswith("assets/flutter_assets/") or name.endswith("/flutter_assets/"):
                    evidence.append(f"flutter: {name}")
                    return True
    except zipfile.BadZipFile:
        pass
    return False


def _is_react_native(apk_path: Path, evidence: list[str]) -> bool:
    """React Native 앱 감지.

    - assets/index.android.bundle (JS 번들)
    - libhermes.so (Hermes JS 엔진)
    - libjsc.so (JavaScriptCore 엔진)
    - libreactnativejni.so
    """
    rn_markers = [
        "assets/index.android.bundle",
        "libhermes.so",
        "libjsc.so",
        "libreactnativejni.so",
        "libreact_",
    ]
    try:
        with zipfile.ZipFile(apk_path) as zf:
            names = zf.namelist()
            for name in names:
                for marker in rn_markers:
                    if marker in name:
                        evidence.append(f"react-native: {name}")
                        return True
    except zipfile.BadZipFile:
        pass
    return False


def _is_compose(apk_path: Path, evidence: list[str]) -> bool:
    """Jetpack Compose 앱 감지.

    모든 dex 파일을 chunk 단위로 스캔. classes.dex가 10MB+ 인 경우가 많아
    앞부분만 보면 놓칠 수 있음.
    """
    compose_markers = [
        b"androidx/compose",
        b"androidx.compose",
    ]
    CHUNK = 2_000_000  # 2MB chunk
    try:
        with zipfile.ZipFile(apk_path) as zf:
            dex_files = [n for n in zf.namelist() if n.endswith(".dex")]
            for dex_name in dex_files:
                try:
                    with zf.open(dex_name) as f:
                        while True:
                            chunk = f.read(CHUNK)
                            if not chunk:
                                break
                            for marker in compose_markers:
                                if marker in chunk:
                                    evidence.append(f"compose: {marker.decode()} in {dex_name}")
                                    return True
                except Exception as e:
                    logger.debug("Failed to read dex %s: %s", dex_name, e)
    except zipfile.BadZipFile:
        pass
    return False
