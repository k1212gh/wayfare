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


@dataclass
class ScreenSignature:
    """Multi-level fingerprint for a UI state."""
    structural_hash: str = ""       # Level 1
    perceptual_hash: str = ""       # Level 2
    gnn_embedding: list[float] = field(default_factory=list)  # Level 3
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
                            screenshot_path: str = "") -> ScreenSignature:
        """Compute all 3 levels of fingerprint for a state."""
        fp = ScreenSignature(activity=activity)

        # Level 1: Structural hash
        fp.structural_hash = self._structural_hash(views, activity)

        # Level 2: pHash (if screenshot available)
        if screenshot_path and Path(screenshot_path).exists():
            fp.perceptual_hash = self._perceptual_hash(screenshot_path)

        # Level 3: HashGNN embedding
        fp.gnn_embedding = self._gnn_hash(views)
        fp.widget_count = len([v for v in views if v.get("clickable")])

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

        Still ignores: free-form `text` (changes with data/language), `bounds`.
        """
        from . import signature_stabilizer
        views = signature_stabilizer.collapse_scroll_children(views)
        structure_parts = []
        accessibility_parts = []
        for v in views:
            cls = v.get("class", "")
            clickable = "C" if v.get("clickable") else ""
            scrollable = "S" if v.get("scrollable") else ""
            editable = "E" if v.get("editable") else ""
            flags = clickable + scrollable + editable
            structure_parts.append(f"{cls}:{flags}")
            # Stable a11y signals — same across visits to the same screen
            rid = v.get("resource_id") or v.get("resource-id") or ""
            desc = v.get("content_desc") or v.get("content-desc") or ""
            if rid or desc:
                accessibility_parts.append(f"{rid}@{desc}")

        raw = (f"{activity}|{'|'.join(sorted(structure_parts))}"
               f"||{'|'.join(sorted(set(accessibility_parts)))}")
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
