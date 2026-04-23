"""Regression tests for server.py defensive patches (M7 / M10 / M8).

Runnable as: `python tests/test_server_hardening.py` from repo root.
Each test prints [PASS] on success and raises on failure.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── M7: _safe_rmtree must not escape the tree ────────────────────

def test_m7_safe_rmtree_deletes_plain_tree():
    from dashboard.backend.server import _safe_rmtree
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "tour"
        (root / "apk").mkdir(parents=True)
        (root / "apk" / "x.apk").write_bytes(b"fake")
        (root / "state.json").write_text("{}", encoding="utf-8")
        _safe_rmtree(root)
        assert not root.exists()
    print("[PASS] test_m7_safe_rmtree_deletes_plain_tree")


def test_m7_safe_rmtree_noop_on_missing():
    from dashboard.backend.server import _safe_rmtree
    with tempfile.TemporaryDirectory() as tmp:
        _safe_rmtree(Path(tmp) / "does_not_exist")
    print("[PASS] test_m7_safe_rmtree_noop_on_missing")


def test_m7_safe_rmtree_does_not_follow_dir_symlink():
    """A symlinked directory INSIDE the tree must not have its contents purged."""
    from dashboard.backend.server import _safe_rmtree
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        outside = tmp / "precious"
        outside.mkdir()
        (outside / "keep.txt").write_text("KEEP", encoding="utf-8")

        tour = tmp / "tour"
        tour.mkdir()
        (tour / "normal.txt").write_text("junk", encoding="utf-8")

        link = tour / "evil_link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Windows w/o developer mode, or filesystem that doesn't support symlinks
            print("[SKIP] test_m7_safe_rmtree_does_not_follow_dir_symlink (no symlink privilege)")
            return

        _safe_rmtree(tour)

        # tour is gone
        assert not tour.exists(), "tour_dir should be removed"
        # but the symlink target's contents are intact
        assert outside.exists(), "symlink target was erroneously removed"
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "KEEP", \
            "symlink target's file was erroneously deleted"
    print("[PASS] test_m7_safe_rmtree_does_not_follow_dir_symlink")


def test_m7_safe_rmtree_refuses_path_outside_workspace():
    """Even if given a real path that's not under the workspace, refuse."""
    from dashboard.backend.server import _safe_rmtree, WORKSPACE_ROOT
    with tempfile.TemporaryDirectory() as tmp:
        outside = Path(tmp) / "some_other_place"
        outside.mkdir()
        (outside / "keep.txt").write_text("KEEP", encoding="utf-8")

        _safe_rmtree(outside, must_be_under=WORKSPACE_ROOT)

        # outside is untouched because it's not under WORKSPACE_ROOT
        assert outside.exists()
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "KEEP"
    print("[PASS] test_m7_safe_rmtree_refuses_path_outside_workspace")


# ─── M10: /api/emulator/kill with serial must not blanket-nuke ───

def test_m10_kill_with_serial_skips_blanket_taskkill():
    from fastapi.testclient import TestClient
    import dashboard.backend.server as srv

    commands = []

    def fake_run(args, **kw):
        commands.append(list(args))
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        client = TestClient(srv.app)
        r = client.post("/api/emulator/kill?serial=emulator-5554&reset_adb=false")
        assert r.status_code == 200, r.text

    # The serial-specific `adb -s ... emu kill` ran
    assert any(
        c[:5] == ["adb", "-s", "emulator-5554", "emu", "kill"] for c in commands
    ), f"expected adb emu kill for the serial, got: {commands}"

    # The blanket `/IM` taskkill did NOT run
    blanket = [c for c in commands if c and c[0] == "taskkill" and "/IM" in c]
    assert not blanket, (
        f"Blanket taskkill /IM fired despite specific serial: {blanket}"
    )
    print("[PASS] test_m10_kill_with_serial_skips_blanket_taskkill")


def test_m10_kill_without_serial_allows_blanket_on_windows():
    if os.name != "nt":
        print("[SKIP] test_m10_kill_without_serial_allows_blanket_on_windows (not Windows)")
        return

    from fastapi.testclient import TestClient
    import dashboard.backend.server as srv

    commands = []

    def fake_run(args, **kw):
        commands.append(list(args))
        m = MagicMock()
        m.returncode = 0
        # Make `adb devices` return no emulators so _list_running_emulators is empty
        m.stdout = "List of devices attached\n"
        m.stderr = ""
        return m

    with patch.object(srv.subprocess, "run", side_effect=fake_run):
        client = TestClient(srv.app)
        r = client.post("/api/emulator/kill?reset_adb=false")
        assert r.status_code == 200, r.text

    # Blanket IS allowed when the caller said "kill everything"
    blanket = [c for c in commands if c and c[0] == "taskkill" and "/IM" in c]
    assert blanket, (
        "Expected blanket taskkill /IM when no serial given, got none"
    )
    print("[PASS] test_m10_kill_without_serial_allows_blanket_on_windows")


# ─── M8: _build_static_screenmap must not emit star-topology edges ───────

def _make_config(workspace_root: Path, tour_id: str):
    """Build a minimal PipelineConfig pointing at a temp workspace."""
    from config import PipelineConfig
    return PipelineConfig(
        apk_path="", tour_id=tour_id,
        workspace_root=str(workspace_root),
    )


def test_m8_static_kg_uses_transition_graph_when_available():
    from dashboard.backend.server import _build_static_screenmap
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jid = "tourA"
        cfg = _make_config(tmp, jid)
        cfg.ensure_dirs()

        (tmp / jid / "static" / "analysis.json").write_text(json.dumps({
            "activities": [
                {"name": "com.ex.MainActivity", "is_launcher": True},
                {"name": "com.ex.SettingsActivity"},
                {"name": "com.ex.DetailActivity"},
            ],
            "entry_activity": "com.ex.MainActivity",
        }), encoding="utf-8")

        (tmp / jid / "static" / "transition_graph.json").write_text(json.dumps({
            "transitions": [
                {"source": "com.ex.MainActivity", "target": "com.ex.SettingsActivity",
                 "kind": "direct", "trigger": "startActivity"},
                # DetailActivity intentionally excluded from transitions
            ],
        }), encoding="utf-8")

        (tmp / jid / "apk" / "metadata.json").write_text(
            json.dumps({"package_name": "com.ex", "version_name": "1.0"}),
            encoding="utf-8",
        )

        _build_static_screenmap(cfg)

        screenmap = json.loads((tmp / jid / "output" / "screen_map.json")
                        .read_text(encoding="utf-8"))
        edges = screenmap["screen_map"]["graph"]["edges"]

        # Exactly one edge (from dex transition graph), not 2 (star to every non-entry)
        assert len(edges) == 1, f"expected 1 transition-driven edge, got {len(edges)}: {edges}"
        e = edges[0]
        assert e["from"] == "com.ex.MainActivity"
        assert e["to"] == "com.ex.SettingsActivity"
    print("[PASS] test_m8_static_kg_uses_transition_graph_when_available")


def test_m8_static_kg_emits_no_edges_without_transition_graph():
    """Without transition_graph.json, edges MUST be empty — no silent star topology."""
    from dashboard.backend.server import _build_static_screenmap
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        jid = "tourB"
        cfg = _make_config(tmp, jid)
        cfg.ensure_dirs()

        (tmp / jid / "static" / "analysis.json").write_text(json.dumps({
            "activities": [
                {"name": "com.ex.MainActivity", "is_launcher": True},
                {"name": "com.ex.SettingsActivity"},
                {"name": "com.ex.DetailActivity"},
            ],
            "entry_activity": "com.ex.MainActivity",
        }), encoding="utf-8")

        (tmp / jid / "apk" / "metadata.json").write_text("{}", encoding="utf-8")

        _build_static_screenmap(cfg)

        screenmap = json.loads((tmp / jid / "output" / "screen_map.json")
                        .read_text(encoding="utf-8"))
        edges = screenmap["screen_map"]["graph"]["edges"]
        nodes = screenmap["screen_map"]["graph"]["nodes"]

        assert len(nodes) == 3, f"expected 3 activity nodes, got {len(nodes)}"
        assert len(edges) == 0, (
            f"expected 0 edges without transition graph, got {len(edges)} — "
            "star topology regression?"
        )
    print("[PASS] test_m8_static_kg_emits_no_edges_without_transition_graph")


# ─── Runner ───────────────────────────────────────────────────────

if __name__ == "__main__":
    test_m7_safe_rmtree_deletes_plain_tree()
    test_m7_safe_rmtree_noop_on_missing()
    test_m7_safe_rmtree_does_not_follow_dir_symlink()
    test_m7_safe_rmtree_refuses_path_outside_workspace()
    test_m10_kill_with_serial_skips_blanket_taskkill()
    test_m10_kill_without_serial_allows_blanket_on_windows()
    test_m8_static_kg_uses_transition_graph_when_available()
    test_m8_static_kg_emits_no_edges_without_transition_graph()
    print()
    print("=== ALL HARDENING TESTS PASSED ===")
