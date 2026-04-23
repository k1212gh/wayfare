"""Framework-aware UI view_tree_readers.

ViewTreeReader abstracts UI element extraction per framework:
  - XMLViewTreeReader:    traditional Android views (resource-id, clickable)
  - ComposeViewTreeReader: Jetpack Compose (text+bounds fallback, clickable=false 대응)
  - FlutterViewTreeReader: Flutter Semantics + OCR fallback (Phase 3-B)
  - RNViewTreeReader:      React Native (accessibility-label 매핑) (Phase 3-B)
"""

from .base import ViewTreeReader
from .xml_view_tree import XMLViewTreeReader
from .compose_view_tree import ComposeViewTreeReader
from .rn_view_tree import RNViewTreeReader
from .flutter_view_tree import FlutterViewTreeReader


def get_reader(framework: str) -> ViewTreeReader:
    """Framework name → Extractor instance.

    Args:
        framework: "xml" | "compose" | "flutter" | "react-native"

    Returns:
        ViewTreeReader instance. Falls back to XMLViewTreeReader for unknown frameworks.
    """
    if framework == "compose":
        return ComposeViewTreeReader()
    if framework in ("react-native", "react_native", "rn"):
        return RNViewTreeReader()
    if framework == "flutter":
        return FlutterViewTreeReader()
    return XMLViewTreeReader()
