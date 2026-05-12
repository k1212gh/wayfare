"""3-Level Screen Signature for intelligent screen coalescing.

Level 1: Structural Hash — XML tree layout (class + depth), ignoring text/content
Level 2: Perceptual Hash (pHash) — screenshot visual similarity
Level 3: HashGNN — graph neural network node embedding similarity

Combined: two states are "same" if ANY level says they match.
"""

import hashlib
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    """Read a float env var, fall back to default on missing/invalid."""
    try:
        raw = os.environ.get(name)
        return float(raw) if raw is not None else default
    except (TypeError, ValueError):
        return default


_BOUNDS_RE = None

# P0-14 (2026-05-07): Android SystemUI overlay 의 status bar 가 dump 에 같이 잡혀
# 시계 분 단위 변화 + 신호 강도 텍스트 변화로 a11y_parts 가 분기되는 문제 방어.
# uiautomator dump 는 foreground app + systemui overlay 를 같이 토해내는데, 캡처
# 시점마다 9:19/9:20 PM 또는 "two bars"/"signal full" 처럼 desc 가 바뀌면 같은
# 화면이 다른 structural_hash 로 갈림. 메가커피 잡 잔여 false-split 5개의 진짜
# 원인. 이 prefix 들로 시작하는 rid 는 a11y_parts 수집에서 제외.
_SYSTEMUI_RID_PREFIXES = (
    "status_bar", "statusicons", "system_icons", "notification",
    "battery", "clock", "wifi", "mobile", "carrier", "airplane",
    "system:id/",   # framework decor / system bars 일반
)


def _is_systemui_rid(rid: str) -> bool:
    if not rid:
        return False
    low = rid.lower()
    return any(low.startswith(p) for p in _SYSTEMUI_RID_PREFIXES)


def _parse_bounds_y_top(bounds: str) -> int | None:
    """Extract y_top from "[x1,y1][x2,y2]" bounds string. None on parse failure."""
    if not bounds:
        return None
    global _BOUNDS_RE
    if _BOUNDS_RE is None:
        import re
        _BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
    m = _BOUNDS_RE.match(bounds)
    if not m:
        return None
    try:
        return int(m.group(2))
    except ValueError:
        return None


@dataclass
class ScreenSignature:
    """Multi-level fingerprint for a UI state."""
    structural_hash: str = ""       # Level 1
    perceptual_hash: str = ""       # Level 2
    gnn_embedding: list[float] = field(default_factory=list)  # Level 3
    screenshot_md5: str = ""        # L0 — byte-equal authoritative override (P0-14)
    activity: str = ""
    widget_count: int = 0


class ScreenSigner:
    """3-level screen signature with configurable similarity thresholds.

    L3 default was 0.95; empirically the count-based fallback vector for
    DeskClock-style apps produced cos-sim > 0.95 across structurally-distinct
    tabs (Clock/Alarms/Timer/Stopwatch), collapsing 364 of 367 events into 3
    bogus "same screen" matches. Default lowered to 0.82; override with
    env ``GNN_SIM_THRESHOLD``. Similarly, L3 is now gated on L1 — if both
    fingerprints have a structural_hash and those differ, L3 cannot override
    (L1 is authoritative when present).
    """

    def __init__(self, phash_threshold: int | None = None, gnn_threshold: float | None = None):
        self.phash_threshold = phash_threshold if phash_threshold is not None \
            else int(_env_float("PHASH_DIST_THRESHOLD", 10))
        self.gnn_threshold = gnn_threshold if gnn_threshold is not None \
            else _env_float("GNN_SIM_THRESHOLD", 0.82)
        self.known_fingerprints: dict[str, ScreenSignature] = {}  # canonical_id → fingerprint
        self._gnn_model = None

    def compute_fingerprint(self, views: list[dict], activity: str,
                            screenshot_path: str = "",
                            *, eager: bool = False) -> ScreenSignature:
        """Compute fingerprint for a state.

        Lazy mode (default) — L1 structural hash + L0 screenshot md5 만 계산.
          L2 (perceptual_hash) 와 L3 (gnn_embedding) 는 비어 둠. 매칭 정책상
          L1 양쪽 있으면 L2/L3 안 보므로 walk 중에는 미계산이 결과 동일하면서
          비용 절약. L1 이 비어있을 때만 L2/L3 도 즉시 계산 (조건적 eager) —
          uiautomator dump 비어있는 캡처에서 폴백이 작동해야 하기 때문.

        Eager mode (``eager=True``) — 모든 신호 계산. Stage 3 → 4 전이 시점에
          ``finalize_fingerprint`` 가 batch 호출하거나, 합성 테스트가 강제 호출.

        비용 (참고):
          - L1 ~ 마이크로초
          - L0 md5 ~ ms (file I/O + 빠른 hash)
          - L2 pHash ~ 수십~수백 ms (PIL decode + DCT)
          - L3 GNN ~ ms (fallback 카운트 벡터) 또는 그 이상 (torch GCN)
        """
        fp = ScreenSignature(activity=activity)

        # Level 1 (always) — 매칭 결정의 거의 99%
        fp.structural_hash = self._structural_hash(views, activity)
        fp.widget_count = len([v for v in views if v.get("clickable")])

        # L0 screenshot md5 (cheap, file I/O) — Stage 6 semantic_merge 가
        # state json 단에서 사용하므로 walk 중에도 항상 계산.
        if screenshot_path and Path(screenshot_path).exists():
            try:
                with open(screenshot_path, "rb") as fh:
                    fp.screenshot_md5 = hashlib.md5(fh.read()).hexdigest()
            except OSError:
                fp.screenshot_md5 = ""

        # Conditional eager: L1 이 비어있다 = 폴백이 필요 = L2/L3 즉시 계산.
        # eager=True 면 강제 모두 계산.
        need_fallback = (not fp.structural_hash) or eager
        if need_fallback:
            if screenshot_path and Path(screenshot_path).exists():
                fp.perceptual_hash = self._perceptual_hash(screenshot_path)
            fp.gnn_embedding = self._gnn_hash(views)

        return fp

    def finalize_fingerprint(self, fp: ScreenSignature, views: list[dict],
                             screenshot_path: str) -> ScreenSignature:
        """Lazy 로 빠진 L2/L3 를 채워넣음. Stage 3 종료 직후 batch 호출.

        Stage 6 semantic_merge 가 ScreenMap node 단계에서 pHash / md5 를 사용하므로
        walk 종료 후엔 모든 fingerprint 가 완성돼 있어야 한다. 이 함수가
        그 차이를 보전한다. 이미 채워진 신호는 건드리지 않는다 (idempotent).
        """
        if not fp.perceptual_hash and screenshot_path and Path(screenshot_path).exists():
            fp.perceptual_hash = self._perceptual_hash(screenshot_path)
        if not fp.gnn_embedding:
            fp.gnn_embedding = self._gnn_hash(views)
        # md5 는 compute 단계에서 이미 채워지지만 누락 보전.
        if not fp.screenshot_md5 and screenshot_path and Path(screenshot_path).exists():
            try:
                with open(screenshot_path, "rb") as fh:
                    fp.screenshot_md5 = hashlib.md5(fh.read()).hexdigest()
            except OSError:
                pass
        return fp

    def find_match(self, fp: ScreenSignature) -> str | None:
        """Check if this fingerprint matches any known state.

        Returns canonical_id if match found, None otherwise.
        """
        for canonical_id, known in self.known_fingerprints.items():
            if self._is_match(fp, known):
                return canonical_id
        return None

    def register(self, canonical_id: str, fp: ScreenSignature) -> None:
        """Register a new canonical state."""
        self.known_fingerprints[canonical_id] = fp

    def get_similarity_score(self, fp1: ScreenSignature, fp2: ScreenSignature) -> float:
        """Get overall similarity score between two fingerprints (0.0 ~ 1.0)."""
        scores = []

        # Level 1: exact structural match = 1.0
        if fp1.structural_hash and fp2.structural_hash:
            scores.append(1.0 if fp1.structural_hash == fp2.structural_hash else 0.0)

        # Level 2: pHash similarity
        if fp1.perceptual_hash and fp2.perceptual_hash:
            dist = self._hamming_distance(fp1.perceptual_hash, fp2.perceptual_hash)
            # 64-bit hash → max distance 64
            scores.append(max(0, 1.0 - dist / 64.0))

        # Level 3: GNN cosine similarity
        if fp1.gnn_embedding and fp2.gnn_embedding:
            scores.append(self._cosine_similarity(fp1.gnn_embedding, fp2.gnn_embedding))

        return max(scores) if scores else 0.0

    def _is_match(self, fp: ScreenSignature, known: ScreenSignature) -> bool:
        """Check if two fingerprints represent the same logical screen.

        L1 is authoritative when BOTH fingerprints have a structural_hash —
        matching hashes = same screen, differing hashes = different screen
        and L2/L3 cannot override. L2/L3 only act as a tiebreaker when L1
        is missing (e.g. empty UI dumps, Canvas-rendered apps).
        """
        have_l1 = bool(fp.structural_hash and known.structural_hash)
        if have_l1:
            # L1 is decisive — same hash wins, different hash stops here.
            # L2/L3 are NOT consulted; they were overriding L1 for apps whose
            # fallback GNN features happen to align across distinct screens.
            return fp.structural_hash == known.structural_hash

        # L1 missing on at least one side — fall through to visual/semantic.
        if fp.perceptual_hash and known.perceptual_hash:
            dist = self._hamming_distance(fp.perceptual_hash, known.perceptual_hash)
            if dist <= self.phash_threshold:
                return True

        if fp.gnn_embedding and known.gnn_embedding:
            sim = self._cosine_similarity(fp.gnn_embedding, known.gnn_embedding)
            if sim >= self.gnn_threshold:
                return True

        return False

    # ─── Level 1: Structural Hash ─────────────────────────

    def _structural_hash(self, views: list[dict], activity: str) -> str:
        """Hash based on UI tree structure.

        Compose-aware: includes stable accessibility signals (content-desc,
        resource-id) because Compose often renders different screens with
        identical class hierarchies (e.g. all ComposeView + AndroidComposeView).
        Without these signals, Home/Search/Library would all hash to the same
        structural_hash and collapse into one node.

        Infinite-scroll-aware: collapses scrollable containers' children via
        `signature_stabilizer.collapse_scroll_children` so Instagram-like feeds
        where every swipe loads new items don't generate per-scroll canonical
        IDs.

        WebView/Flutter-aware (P0-12, 2026-05-06): when accessibility signals
        are missing (every clickable view has empty rid+desc — typical of
        WebView-rendered Compose screens like 메가커피), fall back to a
        layout signature derived from clickable views' Y-bucket positions.
        Without this, Home / 메가오더 / 이벤트 / 전체메뉴 all collapse to the
        same canonical because their class hierarchies are identical (all
        anonymous Views) — walk gets stall thinking every click "didn't move",
        triggers BACK loop, and force-stops indefinitely.

        WebView count-jitter fix (P0-14, 2026-05-07): structure_parts used to
        be a sorted list, so anonymous View 1~2개 차이가 그대로 다른 hash 로
        분기됐다 (메가커피 6caa9768 잡 — 같은 "스탬프 유의사항" 5번 캡처가
        107/109/110/111 view 수 차이로 5개 다른 canonical 로 갈림). 이제는
        (class, flags) 별 Counter 로 집계 + log-scale bucket 으로 quantize 해
        ±1~2 흔들림은 흡수하되 1↔10 같은 의미있는 카운트 차이는 분기 유지.

        Layout fallback gate also relaxed (P0-14): old gate `n_click >= 5 and
        n_a11y_unique <= 2` 가 4-clickable WebView (스탬프 유의사항: 이전/새로고침/
        stampNotice/닫기) 를 못 잡아 폴백 미발동했음. 새 게이트 `n_click >= 3
        and n_a11y_unique < n_click` 로 “a11y 가 빈약한 모든 화면” 을 커버.

        Still ignores: free-form `text` (changes with data/language), exact
        `bounds` (we quantize Y to 50px buckets so minor pixel shifts don't
        explode canonical count).
        """
        from collections import Counter
        from . import signature_stabilizer
        views = signature_stabilizer.collapse_scroll_children(views)
        # P0-14: count (class, flags) tokens then log-bucket the count so that
        # WebView render-progress jitter (107 vs 109 anonymous View) hashes the
        # same. Log-bucket boundaries were chosen so 1↔10 (정적 vs 동적 리스트)
        # 같은 진짜 의미 차이는 다른 bucket 으로 떨어져 false merge 위험 최소.
        structure_counter: Counter = Counter()
        accessibility_parts: list[str] = []
        clickable_y_buckets: list[int] = []  # P0-12 layout fallback signal
        for v in views:
            cls = v.get("class", "")
            clickable = "C" if v.get("clickable") else ""
            scrollable = "S" if v.get("scrollable") else ""
            editable = "E" if v.get("editable") else ""
            flags = clickable + scrollable + editable
            structure_counter[f"{cls}:{flags}"] += 1
            # Stable a11y signals — same across visits to the same screen.
            # P0-14: SystemUI overlay (status bar 시계·신호) rid 는 캡처마다 desc 가
            # 바뀌므로 a11y 수집에서 제외. 그리고 desc 는 stabilize_content_desc 로
            # 시간 토큰 마스킹 거쳐 ticking clock 변동 흡수.
            rid = v.get("resource_id") or v.get("resource-id") or ""
            desc = v.get("content_desc") or v.get("content-desc") or ""
            if _is_systemui_rid(rid):
                pass  # systemui overlay — drop entirely
            elif rid or desc:
                stable_desc = signature_stabilizer.stabilize_content_desc(desc)
                accessibility_parts.append(f"{rid}@{stable_desc}")
            # Layout fallback: collect clickable views' Y position (50px bucket)
            if v.get("clickable"):
                bounds = v.get("bounds", "")
                y_top = _parse_bounds_y_top(bounds)
                if y_top is not None:
                    clickable_y_buckets.append(y_top // 50)

        def _bucket(n: int) -> str:
            # log-scale bucket: 1, 2, 3-4, 5-9, 10-19, 20-49, 50-99, 100+
            if n <= 0:   return "0"
            if n == 1:   return "1"
            if n == 2:   return "2"
            if n <= 4:   return "3-4"
            if n <= 9:   return "5-9"
            if n <= 19:  return "10-19"
            if n <= 49:  return "20-49"
            if n <= 99:  return "50-99"
            return "100+"

        structure_tokens = sorted(
            f"{token}#{_bucket(count)}" for token, count in structure_counter.items()
        )

        # P0-14: 폴백 게이트 완화. 4-clickable WebView 같은 a11y 빈약 화면도 커버.
        layout_part = ""
        n_click = sum(1 for v in views if v.get("clickable"))
        n_a11y_unique = len(set(accessibility_parts))
        if n_click >= 3 and n_a11y_unique < n_click:
            # Sort + tuple for stable hash; multiset = same view set, same hash
            layout_part = "|".join(str(b) for b in sorted(clickable_y_buckets))

        raw = (f"{activity}|{'|'.join(structure_tokens)}"
               f"||{'|'.join(sorted(set(accessibility_parts)))}"
               f"||L:{layout_part}")
        return hashlib.sha256(raw.encode()).hexdigest()

    # ─── Level 2: Perceptual Hash ─────────────────────────

    def _perceptual_hash(self, screenshot_path: str) -> str:
        """Compute perceptual hash of screenshot.

        pHash is robust to:
        - Minor text changes (different data, same layout)
        - Color variations
        - Small UI element changes
        """
        try:
            import imagehash
            from PIL import Image
            img = Image.open(screenshot_path)
            # Crop status bar and nav bar (top 5%, bottom 8%)
            w, h = img.size
            img = img.crop((0, int(h * 0.05), w, int(h * 0.92)))
            return str(imagehash.phash(img, hash_size=8))
        except ImportError:
            logger.debug("imagehash not installed, skipping pHash")
            return ""
        except Exception as e:
            logger.debug("pHash failed: %s", e)
            return ""

    def _hamming_distance(self, h1: str, h2: str) -> int:
        """Hamming distance between two hex hash strings."""
        try:
            import imagehash
            return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
        except ImportError:
            # Manual fallback
            b1 = bin(int(h1, 16))[2:]
            b2 = bin(int(h2, 16))[2:]
            max_len = max(len(b1), len(b2))
            b1 = b1.zfill(max_len)
            b2 = b2.zfill(max_len)
            return sum(c1 != c2 for c1, c2 in zip(b1, b2))

    # ─── Level 3: HashGNN ─────────────────────────────────

    def _gnn_hash(self, views: list[dict]) -> list[float]:
        """Compute GNN-based node embedding for the UI state.

        Uses a lightweight graph where:
        - Each view is a node with features: [is_clickable, is_scrollable, is_editable, class_hash]
        - Edges connect parent→child views (tree structure)

        Falls back to a simple feature vector if torch_geometric unavailable.
        """
        try:
            return self._gnn_hash_torch(views)
        except ImportError:
            return self._gnn_hash_fallback(views)

    def _gnn_hash_torch(self, views: list[dict]) -> list[float]:
        """GNN embedding using torch_geometric."""
        import torch
        import torch.nn.functional as F
        from torch_geometric.nn import GCNConv, global_mean_pool
        from torch_geometric.data import Data

        if len(views) == 0:
            return [0.0] * 16

        # Build node features
        node_features = []
        for v in views:
            cls_hash = hash(v.get("class", "")) % 100 / 100.0
            node_features.append([
                float(v.get("clickable", False)),
                float(v.get("scrollable", False)),
                float(v.get("editable", False)),
                cls_hash,
            ])

        x = torch.tensor(node_features, dtype=torch.float)

        # Build edges (sequential for now — proper tree requires parent info)
        edges = []
        for i in range(len(views) - 1):
            edges.append([i, i + 1])
            edges.append([i + 1, i])
        edge_index = torch.tensor(edges, dtype=torch.long).t() if edges else torch.zeros((2, 0), dtype=torch.long)

        data = Data(x=x, edge_index=edge_index, batch=torch.zeros(len(views), dtype=torch.long))

        # Simple 2-layer GCN → mean pool → 16-dim embedding
        if self._gnn_model is None:
            self._gnn_model = _SimpleGCN(in_channels=4, hidden=32, out_channels=16)
            self._gnn_model.eval()

        with torch.no_grad():
            embedding = self._gnn_model(data)

        return embedding.squeeze().tolist()

    def _gnn_hash_fallback(self, views: list[dict]) -> list[float]:
        """Fallback: simple feature vector when torch unavailable."""
        if not views:
            return [0.0] * 16

        # Count-based features
        n_total = len(views)
        n_clickable = sum(1 for v in views if v.get("clickable"))
        n_scrollable = sum(1 for v in views if v.get("scrollable"))
        n_editable = sum(1 for v in views if v.get("editable"))
        n_text = sum(1 for v in views if v.get("text"))

        # Class distribution (top classes hashed)
        class_counts: dict[str, int] = {}
        for v in views:
            cls = v.get("class", "unknown")
            class_counts[cls] = class_counts.get(cls, 0) + 1

        top_classes = sorted(class_counts.items(), key=lambda x: -x[1])[:8]
        class_features = [count / max(n_total, 1) for _, count in top_classes]
        class_features += [0.0] * (8 - len(class_features))

        # Normalize
        features = [
            n_total / 100.0,
            n_clickable / max(n_total, 1),
            n_scrollable / max(n_total, 1),
            n_editable / max(n_total, 1),
            n_text / max(n_total, 1),
            len(class_counts) / max(n_total, 1),
            # Unique resource-ids ratio
            len({v.get("resource_id", "") for v in views if v.get("resource_id")}) / max(n_total, 1),
            0.0,  # padding
        ] + class_features

        return features[:16]

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Cosine similarity between two vectors."""
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


# ─── GNN Model (used by Level 3) ─────────────────────────

try:
    import torch
    import torch.nn.functional as F
    from torch_geometric.nn import GCNConv, global_mean_pool

    class _SimpleGCN(torch.nn.Module):
        """2-layer GCN for UI state embedding."""
        def __init__(self, in_channels=4, hidden=32, out_channels=16):
            super().__init__()
            self.conv1 = GCNConv(in_channels, hidden)
            self.conv2 = GCNConv(hidden, out_channels)

        def forward(self, data):
            x, edge_index, batch = data.x, data.edge_index, data.batch
            x = F.relu(self.conv1(x, edge_index))
            x = self.conv2(x, edge_index)
            return global_mean_pool(x, batch)

except ImportError:
    # torch_geometric not available — fallback mode only
    class _SimpleGCN:
        pass
