"""App walk — TapWalker (default) or DroidBot fallback."""

import subprocess
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def run_droidbot(
    apk_path: str,
    device_serial: str,
    output_dir: str,
    timeout: int = 600,
    policy: str = "dfs_greedy",
    is_emulator: bool = True,
    event_count: int = 800,  # F3 (2026-05-02): 500 → 800. timeout 30분 매칭
    framework: str = "xml",
) -> None:
    """Launch app walk.

    WALK_MODE env var:
      smart    = TapWalker (unseen-driven, default)
      droidbot = Original DroidBot subprocess
    """
    mode = os.environ.get("WALK_MODE", "tap").lower()

    if mode == "tap":
        logger.info("Using TapWalker (unseen-driven, framework=%s)", framework)
        _run_smart(apk_path, device_serial, output_dir, timeout, event_count, framework)
    else:
        logger.info("Using DroidBot (%s)", policy)
        _run_droidbot(apk_path, device_serial, output_dir, timeout, policy, is_emulator, event_count)


def _run_smart(apk_path, device_serial, output_dir, timeout, event_count, framework="xml"):
    from .tap_walker import TapWalker
    walker = TapWalker(
        device_serial=device_serial,
        apk_path=apk_path,
        output_dir=output_dir,
        timeout=timeout,
        max_events=event_count,
        framework=framework,
    )
    result = walker.run()
    stats = result.get("stats", {})
    logger.info("TapWalker done: %d events, %d screens, %.0fs (framework=%s)",
                stats.get("total_events", 0),
                stats.get("unique_structures", 0),
                stats.get("elapsed_seconds", 0),
                framework)


def _run_droidbot(apk_path, device_serial, output_dir, timeout, policy, is_emulator, event_count):
    args = [
        "-a", apk_path, "-d", device_serial, "-o", output_dir,
        "-policy", policy, "-timeout", str(timeout), "-count", str(event_count),
        "-grant_perm", "-keep_env", "-keep_app", "-accessibility_auto",
    ]
    if is_emulator:
        args.append("-is_emulator")

    cmd = [
        sys.executable, "-c",
        "import sys; sys.argv = ['droidbot'] + sys.argv[1:]; from droidbot.start import main; main()",
        *args,
    ]

    log_path = Path(output_dir) / "droidbot_log.txt"
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    with open(log_path, "w") as log_file:
        try:
            subprocess.run(cmd, stdout=log_file, stderr=subprocess.STDOUT, timeout=timeout + 300)
        except subprocess.TimeoutExpired:
            logger.warning("DroidBot timed out")
