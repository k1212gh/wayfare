"""SQLite-based UI pattern cache for cross-app element reuse.

When LLM analyzes a UI element (e.g., "EditText with content-desc='Search'"),
the result is cached by a signature hash. Next time any app has a similar element,
the cached result is returned without LLM call.

Signature = sha256(class_name + normalized_content_desc + sorted_action_types)
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "cache" / "widget_cache.db"


class WidgetCache:
    """Cross-app UI pattern cache backed by SQLite."""

    def __init__(self, db_path: Path | str | None = None, enabled: bool | None = None):
        if enabled is None:
            enabled = os.environ.get("CACHE_ENABLED", "true").lower() == "true"
        self.enabled = enabled

        if not self.enabled:
            self.conn = None
            return

        db = Path(db_path) if db_path else _DB_PATH
        db.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db))
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS ui_patterns (
                signature TEXT PRIMARY KEY,
                class_name TEXT,
                content_desc TEXT,
                text_label TEXT,
                role TEXT,
                action_type TEXT,
                expected_result TEXT,
                apps_seen TEXT DEFAULT '[]',
                hit_count INTEGER DEFAULT 0,
                confidence TEXT DEFAULT 'medium',
                created_at TEXT,
                updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_class ON ui_patterns(class_name);
            CREATE INDEX IF NOT EXISTS idx_confidence ON ui_patterns(confidence);
        """)
        self.conn.commit()

    @staticmethod
    def compute_signature(class_name: str, content_desc: str = "",
                          text: str = "", action_types: list[str] | None = None) -> str:
        """Compute cache key from element's functional attributes."""
        # Normalize
        cls = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name
        desc = content_desc.strip().lower()
        txt = text.strip().lower()[:30]  # Limit text to avoid unique-per-item
        actions = "|".join(sorted(action_types or []))

        raw = f"{cls}|{desc}|{txt}|{actions}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def lookup(self, class_name: str, content_desc: str = "",
               text: str = "", action_types: list[str] | None = None) -> Optional[dict]:
        """Look up a cached element analysis. Returns dict or None."""
        if not self.enabled or not self.conn:
            return None

        sig = self.compute_signature(class_name, content_desc, text, action_types)
        row = self.conn.execute(
            "SELECT * FROM ui_patterns WHERE signature = ?", (sig,)
        ).fetchone()

        if row:
            # Increment hit count
            self.conn.execute(
                "UPDATE ui_patterns SET hit_count = hit_count + 1, updated_at = ? WHERE signature = ?",
                (datetime.now(timezone.utc).isoformat(), sig)
            )
            self.conn.commit()
            logger.debug("Cache HIT: %s (%s) → role=%s", class_name, content_desc[:20], row["role"])
            return dict(row)

        return None

    def store(self, class_name: str, content_desc: str, text: str,
              action_types: list[str], role: str, expected_result: str = "",
              confidence: str = "medium", package_name: str = "") -> None:
        """Store an LLM analysis result in cache. Only stores high/medium confidence."""
        if not self.enabled or not self.conn:
            return
        if confidence == "low":
            return  # Don't cache low-confidence results

        sig = self.compute_signature(class_name, content_desc, text, action_types)
        now = datetime.now(timezone.utc).isoformat()

        # Check if exists — update apps_seen
        existing = self.conn.execute(
            "SELECT apps_seen FROM ui_patterns WHERE signature = ?", (sig,)
        ).fetchone()

        if existing:
            apps = json.loads(existing["apps_seen"])
            if package_name and package_name not in apps:
                apps.append(package_name)
                self.conn.execute(
                    "UPDATE ui_patterns SET apps_seen = ?, updated_at = ? WHERE signature = ?",
                    (json.dumps(apps), now, sig)
                )
        else:
            apps = [package_name] if package_name else []
            self.conn.execute(
                """INSERT INTO ui_patterns
                   (signature, class_name, content_desc, text_label, role,
                    action_type, expected_result, apps_seen, confidence, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sig, class_name, content_desc, text, role,
                 "|".join(action_types), expected_result,
                 json.dumps(apps), confidence, now, now)
            )

        self.conn.commit()
        logger.debug("Cache STORE: %s (%s) → role=%s", class_name, content_desc[:20], role)

    def stats(self) -> dict:
        """Get cache statistics."""
        if not self.enabled or not self.conn:
            return {"enabled": False}

        total = self.conn.execute("SELECT COUNT(*) as c FROM ui_patterns").fetchone()["c"]
        hits = self.conn.execute("SELECT SUM(hit_count) as c FROM ui_patterns").fetchone()["c"] or 0
        multi_app = self.conn.execute(
            "SELECT COUNT(*) as c FROM ui_patterns WHERE json_array_length(apps_seen) > 1"
        ).fetchone()["c"]

        return {
            "enabled": True,
            "total_patterns": total,
            "total_hits": hits,
            "multi_app_patterns": multi_app,
        }

    def close(self):
        if self.conn:
            self.conn.close()
