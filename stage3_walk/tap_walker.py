"""Smart Walker — priority-based walk that avoids redundant visits.

Instead of DroidBot's blind dfs_greedy, this controller:
1. Tracks visited states by structure_str (not just state_str)
2. Prioritizes clicks that lead to NEW screens (unseen structure)
3. Penalizes actions on already-walked pages
4. Auto-backs out of dead-end loops
5. Favors deeper navigation over lateral (list item) walk
"""

import json
import logging
import os
import re
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from . import view_tree_parser
from .mixins import CaptureMixin, DeviceSessionMixin, GuardsMixin, ScanMixin

logger = logging.getLogger(__name__)


class TapWalker(ScanMixin, CaptureMixin, GuardsMixin, DeviceSessionMixin):
    """Step-by-step app walk with unseen-driven priority."""

    def __init__(self, device_serial: str, apk_path: str, output_dir: str,
                 timeout: int = 600, max_events: int = 500, framework: str = "xml"):
        self.device_serial = device_serial
        self.apk_path = apk_path
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self.max_events = max_events
        self.framework = framework
        # P0-5 (2026-05-04): coverage_target 활용 — must_reach 화면 도달률
        # ≥ 이 값이면 timeout 안 기다리고 즉시 종료. 메가커피 잡 30분 idle
        # 방지 + 일찍 끝난 잡 진단 가능. config.py:49 의 PipelineConfig 기본값
        # (0.8) 을 따른다.
        try:
            from config import PipelineConfig  # type: ignore
            self.coverage_target = float(PipelineConfig.coverage_target)
        except Exception:
            self.coverage_target = 0.8

        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "states").mkdir(exist_ok=True)

        # Cancellation: dashboard/backend writes <tour>/cancel.flag, sibling of output_dir
        self._cancel_flag = self.output_dir.parent / "cancel.flag"
        # Pause: <tour>/paused.json — walker writes on auto-detect, user removes via Resume
        self._paused_file = self.output_dir.parent / "paused.json"
        self._pause_max_seconds = 600  # 10 min timeout

        # Framework-aware UI Extractor
        from .view_tree_readers import get_reader
        self.extractor = get_reader(framework)
        logger.info("Using %s extractor for framework=%s",
                    type(self.extractor).__name__, framework)

        # 3-Level State Hasher — defaults now come from env (see ScreenSigner
        # docstring). L3 threshold lowered to 0.82 and L1 is authoritative when
        # present, so DeskClock-style Fragment tabs stop collapsing into one node.
        from .screen_signer import ScreenSigner
        self.hasher = ScreenSigner()

        # Walk state
        self.visited_structures: dict[str, int] = defaultdict(int)  # canonical_id → visit count
        self.visited_screens: set[str] = set()
        self.canonical_map: dict[str, str] = {}  # raw_hash → canonical_id
        self.action_history: list[dict] = []
        self.tried_actions: dict[str, set[str]] = defaultdict(set)  # canonical_id → set of tried action descs
        # 2026-04-29: popup item 은 어느 fragment 에서 띄워도 같은 popup (overflow
        # menu 의 Settings/Help 등) — canonical_id 가 background fragment 따라
        # 달라져서 tried 매번 reset 되는 문제. popup item 의 action_desc
        # (label@bounds) 는 fragment 무관 같으니 cross-fragment 로 추적.
        self.tried_popup_items: set[str] = set()
        self.states: list[dict] = []
        self.transitions: list[dict] = []
        self.back_count = 0
        self.stall_count = 0
        self.last_canonical = ""
        self.hash_stats = {"l1_matches": 0, "l2_matches": 0, "l3_matches": 0, "new_screens": 0}
        # Trap instrumentation
        self.trap_stats: dict[str, int] = defaultdict(int)
        # Per-canonical RecyclerView tap counter — limit to 3 before forcing scroll
        self.rv_taps_per_screen: dict[str, int] = defaultdict(int)
        # Per-canonical scroll budget — cap at SCROLL_CAP scrolls per screen so we
        # don't get stall in infinite-scroll feeds (Instagram-like) where every
        # swipe loads new items and stall_count never trips. Set of canonicals
        # that hit a "no more content" boundary (Δviews ≤ 3 after swipe).
        self.scroll_count_per_screen: dict[str, int] = defaultdict(int)
        # 2026-04-30: per-list_view visit counter — "리스트뷰 1개만 들어가서 일반화"
        # 정책. group_id 는 (canonical_id, list_view_group_id) 조합으로 cross-screen
        # 충돌 회피. 첫 항목 normal score, 2번째부터 list_view_redundancy_penalty.
        self.list_view_visit_count: dict[str, int] = defaultdict(int)
        # 2026-05-01: external page detected → 직전 trigger blacklist (W3).
        # score_action 이 blacklist trigger 발견 시 강 페널티 → 같은 메뉴 재클릭 안 함.
        self.external_blacklist: set[str] = set()
        # P0-10f (2026-05-05): structure_str hash 기반 영구 학습.
        # 키워드/desc 같은 표면 매칭 (P0-10d/e 폐기) 이 아닌 view tree
        # 구조 hash. 같은 화면이면 잡 마다 동일 hash. desc/문구 무관하게
        # 의미 기반 매칭. 디스크 저장 (앱별) → 다음 잡 시작 시 자동 학습.
        # 파일: workspace/_learned/{package}.json
        self._learned_path: Path | None = None
        self._learned_structures: set[str] = set()
        # 2026-05-03 (R6): multi-target action 페널티 — 같은 (canonical, action_desc)
        # 가 ≥3 다른 target canonical 로 transition 했으면 random transition 으로 판정.
        # 메가커피 이벤트 webview 의 wrapper element 가 21 incoming hub 되는 회귀 차단.
        # key = (canonical_id, action_desc) → set of target canonical_ids
        from collections import defaultdict as _dd
        self.action_target_diversity: dict[tuple, set] = _dd(set)
        self.scrollable_exhausted: set[str] = set()

        # ---- Static-graph-guided walk plan ----
        # Loaded in run() from <tour>/output/screen_map.json (wireframe ScreenMap).
        # Priority list of Activity FQNs we still want to visit via direct
        # am start (explicit intent).  Filled from nodes with status=declared.
        self.plan_targets: list[str] = []
        self.plan_visited: set[str] = set()
        self.plan_failed: set[str] = set()

        # ---- Package guard ----
        # Set from APK package name on run() entry. Used to detect when a tap
        # sent us into an external app (Gmail via "Send feedback", Chrome via
        # a privacy-policy link, etc.) so we can bounce back and not waste
        # walk budget in someone else's UI.
        self.target_package: str = ""
        # Disable guard via env var when the target app legitimately uses
        # Chrome Custom Tabs for OAuth etc. — set ALLOW_EXTERNAL=1 to opt out.
        self._allow_external = os.environ.get("ALLOW_EXTERNAL", "").lower() in (
            "1", "true", "yes",
        )

        # Vision-LLM Clicker fallback (opt-in via VISION_CLICKER_ENABLED=1).
        # Cycle 0~3 (4/29~30) evidence: score 가중치 누적은 TimePicker OK 같은
        # outside-view stall 을 못 풂. Vision LLM 이 화면 보고 actionable 좌표
        # 추정 → click 으로 우회. budget 기본 10/잡 ($0.005~0.01 수준).
        self.vision_tapper = None
        if os.environ.get("VISION_CLICKER_ENABLED", "").lower() in ("1", "true", "yes"):
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if api_key and "PLACEHOLDER" not in api_key:
                try:
                    from .vision_tapper import VisionTapper
                    self.vision_tapper = VisionTapper(api_key=api_key)
                    logger.info(
                        "[vision] Clicker enabled (budget=%d, model=%s)",
                        self.vision_tapper.budget, self.vision_tapper.model,
                    )
                except Exception as e:
                    logger.warning("[vision] init failed: %s — fallback disabled", e)
            else:
                logger.warning("[vision] VISION_CLICKER_ENABLED but ANTHROPIC_API_KEY missing")

        # 2026-04-30: ViewTreeChain + StallDetector — 2-tier fallback.
        # ViewTreeChain 의 is_primary_sufficient = framework-specific quality
        # (entry 시점 정적 검사). StallDetector = runtime 동적 (T2/T3/T4).
        # vision_tapper 없으면 둘 다 no-op (graceful degradation).
        from .view_tree_chain import ViewTreeChain
        from .stall_detector import StallDetector
        self._view_tree_chain = ViewTreeChain(
            framework=framework,
            primary_reader=self.extractor,
            vision_tapper=self.vision_tapper,
        )
        self._stall_detector = StallDetector()

    def run(self) -> dict:
        """Run the smart walk loop."""
        import subprocess

        logger.info("Smart Walker: device=%s, timeout=%ds, max_events=%d",
                     self.device_serial, self.timeout, self.max_events)

        # Medium-term mitigation: turn off system-level animation scales so
        # window transitions finish instantly. Helps uiautomator's idle
        # detection AND speeds the walker up in general. App-controlled
        # animations (ticking clocks, ad carousels) are NOT affected — those
        # are handled by the uiautomator2 path in u2_helper.
        from . import u2_helper
        u2_helper.disable_animations(self.device_serial)
        # Warm up u2 — first connect triggers agent APK install on the device
        # (one-time), so better to pay that cost upfront than mid-walk.
        u2_helper._connect_u2(self.device_serial)
        try:
            return self._run_body()
        finally:
            u2_helper.restore_animations(self.device_serial)

    def _run_body(self) -> dict:
        """Body of run(), wrapped so the caller can set up/tear down."""
        import subprocess

        start_time = time.time()
        event_count = 0

        apk_path = Path(self.apk_path).resolve()

        # Figure out target package upfront so we can check "already installed" on real devices
        from androguard.core.apk import APK
        apk = APK(self.apk_path)
        package = apk.get_package()
        # Stash for the package guard (_check_app_bounds). Set to empty means
        # guard is effectively off regardless of the ALLOW_EXTERNAL flag.
        self.target_package = package or ""

        is_emulator = self._is_emulator_device()

        # If the same package is already installed on the device/emulator,
        # skip reinstall — preserves login state and avoids
        # INSTALL_FAILED_DUPLICATE_PACKAGE when a prior session is pending.
        already_installed = False
        if package:
            try:
                check = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell", "pm", "path", package],
                    capture_output=True, timeout=10)
                if check.returncode == 0 and (check.stdout or b"").decode("utf-8", errors="replace").strip().startswith("package:"):
                    already_installed = True
            except Exception:
                pass

        if is_emulator and not already_installed:
            # Emulator: install fresh only if the package isn't already there.
            sibling_apks = list(apk_path.parent.glob("*.apk"))

            # adb 36.x on Windows has a split-name derivation bug for
            # install-multiple when the APK path contains non-ASCII
            # characters: PackageInstaller receives "." as the session-write
            # name and rejects it with `Invalid name: .`. Our workspace path
            # is `.\...`, so every split-APK
            # install hit this. Workaround: stage APKs in an ASCII-only
            # tempdir before calling adb.
            needs_ascii_staging = any(not str(p).isascii() for p in sibling_apks)

            import shutil as _shutil
            import tempfile as _tempfile
            staging_dir: Path | None = None
            try:
                if needs_ascii_staging:
                    staging_dir = Path(_tempfile.mkdtemp(prefix="sa_install_"))
                    staged: list[Path] = []
                    for a in sibling_apks:
                        dst = staging_dir / a.name
                        _shutil.copy2(a, dst)
                        staged.append(dst)
                    install_sources = staged
                    staged_apk_path = staging_dir / apk_path.name
                else:
                    install_sources = sibling_apks
                    staged_apk_path = apk_path

                if len(install_sources) > 1:
                    logger.info("Emulator — installing %d split APKs: %s%s",
                                 len(install_sources),
                                 [a.name for a in install_sources],
                                 " (via ASCII staging)" if needs_ascii_staging else "")
                    install_result = subprocess.run(
                        ["adb", "-s", self.device_serial, "install-multiple", "-r"]
                        + [str(a) for a in install_sources],
                        capture_output=True, timeout=240)
                else:
                    logger.info("Emulator — installing single APK: %s%s",
                                staged_apk_path.name,
                                " (via ASCII staging)" if needs_ascii_staging else "")
                    install_result = subprocess.run(
                        ["adb", "-s", self.device_serial, "install", "-r", str(staged_apk_path)],
                        capture_output=True, timeout=180)
            finally:
                if staging_dir is not None:
                    _shutil.rmtree(staging_dir, ignore_errors=True)
            install_stdout = (install_result.stdout or b"").decode("utf-8", errors="replace")
            install_stderr = (install_result.stderr or b"").decode("utf-8", errors="replace")
            if install_result.returncode != 0 or "Success" not in install_stdout:
                raise RuntimeError(
                    f"APK install failed (rc={install_result.returncode}): "
                    f"{install_stderr.strip() or install_stdout.strip()}"
                )
            logger.info("Install OK: %s", install_stdout.strip().splitlines()[-1] if install_stdout else "Success")
        else:
            # Real device: do NOT reinstall, but verify the app is already there
            if not package:
                raise RuntimeError("Could not read package name from APK")
            check = subprocess.run(
                ["adb", "-s", self.device_serial, "shell", "pm", "path", package],
                capture_output=True, timeout=10)
            out = (check.stdout or b"").decode("utf-8", errors="replace").strip()
            if check.returncode != 0 or not out.startswith("package:"):
                raise RuntimeError(
                    f"Real device: app '{package}' is not installed. "
                    "Install it on the device first, or use an emulator."
                )
            logger.info("Package %s already installed — skipping install (preserves login state)", package)

        # (package/main_activity for launch)
        main_activity = apk.get_main_activity()
        if main_activity and not main_activity.startswith(package):
            if main_activity.startswith("."):
                main_activity = package + main_activity

        self.package = package
        self.main_activity = main_activity

        # P0-10f (2026-05-05): structure_str 기반 학습 로드. 이전 잡들이
        # auth/external/결제 화면으로 검출한 화면의 view tree hash 를 디스크
        # 에서 읽음 → 같은 구조 화면 도달 시 즉시 BACK. 단어/desc 무관.
        # A-1+A-3 (2026-05-06): 앱간 글로벌 도메인 블랙리스트 — 모든 앱이
        # 학습한 외부 도메인 set. 새 앱 시작 시 자동 로드 → 첫 dump 차단.
        self._global_domains: set[str] = set()
        self._learned_dir: Path | None = None
        try:
            learned_dir = self.output_dir.parent.parent / "_learned"
            learned_dir.mkdir(parents=True, exist_ok=True)
            self._learned_dir = learned_dir
            self._learned_path = learned_dir / f"{package}.json"
            if self._learned_path.exists():
                data = json.loads(self._learned_path.read_text(encoding="utf-8"))
                self._learned_structures = set(data.get("blocked_structures", []))
                if self._learned_structures:
                    logger.info(
                        "[learned] %d structure hashes loaded from %s — first-touch skip",
                        len(self._learned_structures),
                        self._learned_path.name,
                    )
            # A-1: 글로벌 도메인 로드 (앱간 학습 누적)
            from .outbound_intent_guard import load_global_domain_blacklist
            self._global_domains = load_global_domain_blacklist(learned_dir)
        except Exception as e:
            logger.debug("[learned] load failed: %s", e)

        # A-5 (2026-05-06): own_domain_parts 자동 추출 — fixture 명시 →
        # manifest intent-filter host → package reverse 순. 메가커피 전용
        # 하드코딩 (DEFAULT_OWN_DOMAIN_PARTS) 의존성 제거.
        self._own_domain_parts: list[str] = []

        # E (2026-05-03): task fixture 키워드 로드 — walk 시점에 task goal 에
        # 등장한 명사 (메뉴/장바구니/매장/MY/쿠폰/...) 가 view text 에 hit 시
        # score 보너스. 메가커피/DeskClock 등 fixture 있으면 자동 적용.
        self.task_keywords: list[str] = []
        # P0-5 (2026-05-04): fixture 의 must_reach 화면들 + 도달 추적.
        # task_coverage 의 분자/분모 와 별개 — walk 단에서 "필수 화면 N 중 K
        # 도달" 측정해 coverage_target 도달 시 즉시 종료.
        self._fixture: dict | None = None
        self.must_reach_specs: list[dict] = []   # [{description, match_any}, ...]
        self.must_reach_hit: set[str] = set()    # description 들 (도달한 것)
        try:
            from stage6_screenmap.task_fixture import load_fixture, extract_keywords
            # package 에서 short app id 추출 (예: co.kr.waldlust.megacoffee → megacoffee)
            app_id = package.rsplit(".", 1)[-1] if package else ""
            fix = load_fixture(app_id)
            if fix:
                self._fixture = fix
                self.task_keywords = extract_keywords(fix)
                if self.task_keywords:
                    logger.info("Task fixture keywords loaded (%d): %s",
                                len(self.task_keywords), self.task_keywords[:8])
                # must_reach: tasks[*].expected_screens 중 must_reach=True 인 화면
                for t in (fix.get("tasks") or []):
                    for s in (t.get("expected_screens") or []):
                        if s.get("must_reach"):
                            self.must_reach_specs.append({
                                "description": s.get("description", "?"),
                                "match_any": s.get("match_any") or {},
                            })
                if self.must_reach_specs:
                    logger.info("must_reach screens: %d (target=%.0f%%)",
                                len(self.must_reach_specs),
                                self.coverage_target * 100)
            # A-5 own_domain 자동 추출 (fixture → manifest → package reverse)
            from .outbound_intent_guard import derive_own_domain_parts
            static_path = self.output_dir.parent / "static" / "analysis.json"
            static_info = None
            if static_path.exists():
                try:
                    static_info = json.loads(static_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            self._own_domain_parts = derive_own_domain_parts(
                package, fixture=fix, static_info=static_info,
            )
            if self._own_domain_parts:
                logger.info("[own_domain] %d parts: %s",
                            len(self._own_domain_parts),
                            self._own_domain_parts[:5])
        except Exception as e:
            logger.debug("Task fixture load failed: %s", e)

        # Pre-grant runtime permissions so first-launch permission dialogs
        # don't block walk.  Silently ignored if a permission isn't
        # applicable (adb returns non-zero but we don't care).
        self._grant_runtime_permissions(package)

        # Framework-specific device setup (e.g. Flutter enables Semantics).
        try:
            self.extractor.prepare_device(self.device_serial)
        except Exception as e:
            logger.warning("Extractor prepare_device failed: %s", e)

        subprocess.run(["adb", "-s", self.device_serial, "shell",
                        "am", "start", "-n", f"{package}/{main_activity}"],
                       capture_output=True, timeout=10)
        # P1 (2026-05-06): RN 은 JS bundle 로드 5-10초 필요. capture 가 그 전에
        # dump 시도하면 빈 view → click → JS bridge 미초기화 → app crash 후
        # NexusLauncher dump (ffd7c579 잡 패턴). framework 별 시작 wait 차등.
        if self.framework == "react-native":
            logger.info("[rn] waiting 8s for JS bundle load")
            time.sleep(8)
        elif self.framework == "flutter":
            # Flutter 도 engine init 시간 필요
            logger.info("[flutter] waiting 4s for engine init")
            time.sleep(4)
        else:
            time.sleep(2)

        # Tier-1 bootstrap: visit obvious navigation entry points BEFORE random walk
        # starts.  This guarantees bottom-tab screens and drawer contents get captured.
        try:
            self._bootstrap_navigation(package, main_activity)
        except Exception as e:
            logger.warning("Bootstrap routine failed: %s", e)

        # ---- Load walk plan from static wireframe ScreenMap ----
        # If Stage 2.5 wrote screen_map.json, extract declared activities
        # (statically reachable ones first) as direct-launch targets.
        try:
            self._load_plan(package)
            if self.plan_targets:
                logger.info("[plan] Loaded %d declared activities as direct-launch targets",
                             len(self.plan_targets))
        except Exception as e:
            logger.warning("Plan load failed: %s", e)

        empty_count = 0  # Track consecutive empty UI dumps
        out_of_app_count = 0  # Guard: if we can't return to target app, bail
        # B 옵션 (2026-04-24): stall-on-page detection — 같은 hash N회 연속이면 hard reset
        prev_struct_hash = ""
        same_hash_streak = 0
        stall_resets = 0  # 무한루프 방지 — 한 run 에 최대 3회만 reset

        # P0-5b (2026-05-04): break 경로별 종료 사유 기록 — _save_results 가
        # 정확히 분류하도록 변수에 명시. 기존 fallback 분류는 잔존하지만 우선순위
        # 가 낮음.
        self._term_reason: str | None = None

        while event_count < self.max_events and (time.time() - start_time) < self.timeout:
            # 0. Cancellation check (cooperative, from /api/tours/{id}/stop)
            if self._cancel_flag.exists():
                logger.info("Cancel flag detected — stopping walk at event %d", event_count)
                self._term_reason = "cancelled"
                break

            # P0-5 (2026-05-04): must_reach 도달률 ≥ coverage_target 이면 종료.
            # 메가커피 잡 30분 timeout idle 방지 — fixture 가 명시한 핵심
            # 화면을 다 봤으면 더 walking 안 하고 끝낸다.
            if self.must_reach_specs:
                hit_ratio = len(self.must_reach_hit) / len(self.must_reach_specs)
                if hit_ratio >= self.coverage_target:
                    logger.info(
                        "[coverage] must_reach %d/%d (%.0f%%) ≥ target %.0f%% — early exit",
                        len(self.must_reach_hit), len(self.must_reach_specs),
                        hit_ratio * 100, self.coverage_target * 100,
                    )
                    self._term_reason = "coverage_target_reached"
                    break

            # 0a. Pause check (manual or auto-detected login) — wait until user Resumes
            if self._paused_file.exists():
                if not self._wait_for_resume():
                    self._term_reason = "paused_then_failed"
                    break  # cancelled during pause or timed out

            # 1. Capture current state
            state = self._capture_screen(event_count)
            if not state:
                self._term_reason = "capture_failed"
                break

            # 1b. Auto-detect login / auth screen → request user input
            # P0-10 (2026-05-05): AUTH_AUTO_BACKOFF (기본 ON) — 메가커피 처럼
            # 본인인증/SMS/raon 보안 키패드 같이 자동화 불가능 wall 만나면
            # PAUSE 대신 BACK + blacklist + walk 계속. 사용자 manual resume
            # 기다리느라 17분 idle 종료되는 dd79a51c 패턴 차단. ENV=0 으로
            # 끄면 기존 PAUSE 정책 유지 (사용자 직접 로그인 가능 시).
            if self._detect_user_input_needed(state):
                auto_backoff = os.environ.get("AUTH_AUTO_BACKOFF", "1") != "0"
                if auto_backoff:
                    canonical_id = state.get("canonical_id") or "auth_screen"
                    if not hasattr(self, "_auth_screens_seen"):
                        self._auth_screens_seen: set[str] = set()
                    if not hasattr(self, "_auth_consecutive_count"):
                        self._auth_consecutive_count: dict[str, int] = {}
                    self._auth_screens_seen.add(canonical_id)
                    # P0-10f: 화면 구조 학습 — view tree hash. desc 무관.
                    if state.get("structure_str"):
                        self._learn_structure(state["structure_str"])
                    # 진입 액션은 in-memory blacklist (이번 잡 안에서만)
                    if self.action_history:
                        last_desc = (self.action_history[-1].get("event_desc")
                                     or self.action_history[-1].get("desc", ""))
                        if last_desc:
                            self.external_blacklist.add(last_desc)
                    self.trap_stats["auth_backoff"] = \
                        self.trap_stats.get("auth_backoff", 0) + 1
                    # P0-10b (2026-05-05): 같은 canonical 에서 auth_backoff 가
                    # ≥3회 연속 발동하면 BACK 이 같은 화면으로 떨어지는
                    # dead-lock (37ebca02 잡 — 507회 stall). force-stop +
                    # 새 launcher 로 끊는다. 같은 auth screen 더 이상 안 가게
                    # last_desc 외 추가 blacklist 도 효과 X 라 강제 종료가 합리적.
                    cnt = self._auth_consecutive_count.get(canonical_id, 0) + 1
                    self._auth_consecutive_count[canonical_id] = cnt
                    logger.info(
                        "[auth_backoff] auth on %s — back + blacklist (consec=%d, total=%d)",
                        canonical_id, cnt, self.trap_stats["auth_backoff"],
                    )
                    if cnt >= 3:
                        # P0-10c (2026-05-05): L3 영구 blocked — force-stop +
                        # relaunch 후에도 같은 canonical 에 또 도달하는 절대
                        # 해결 불가 케이스 (KB ARS 결제 + 취소 다이얼로그 같이
                        # BACK / 키보드 입력 / 우회 모두 안 통하는 외부 게이트
                        # 웨이) 를 영구 blocked 로 마킹. 이후 같은 canonical
                        # 도달 시 즉시 BACK + 진입 액션 누적 blacklist.
                        if not hasattr(self, "_permanent_blocked_canonicals"):
                            self._permanent_blocked_canonicals: set[str] = set()
                        if canonical_id in self._permanent_blocked_canonicals:
                            logger.warning(
                                "[auth_backoff] canonical %s already permanently blocked but reached again — likely fixture/walk loop",
                                canonical_id,
                            )
                        self._permanent_blocked_canonicals.add(canonical_id)
                        logger.info(
                            "[auth_backoff] dead-lock on %s (consec %d) — force-stop + relaunch + permanent block",
                            canonical_id, cnt,
                        )
                        try:
                            subprocess.run(
                                ["adb", "-s", self.device_serial, "shell",
                                 "am", "force-stop", package],
                                capture_output=True, timeout=5,
                            )
                            time.sleep(0.8)
                            subprocess.run(
                                ["adb", "-s", self.device_serial, "shell",
                                 "am", "start", "-n", f"{package}/{main_activity}"],
                                capture_output=True, timeout=15,
                            )
                            time.sleep(2.0)
                        except Exception as e:
                            logger.warning("[auth_backoff] force-stop failed: %s", e)
                        self._auth_consecutive_count[canonical_id] = 0
                        self.back_count = 0
                        self.stall_count = 0
                        # 너무 많이 발동하면 아예 종료
                        if self.trap_stats["auth_backoff"] >= 30:
                            logger.warning(
                                "[auth_backoff] %d total escapes — terminating",
                                self.trap_stats["auth_backoff"],
                            )
                            self._term_reason = "auth_backoff_exhausted"
                            break
                        event_count += 1
                        continue
                    if not self._press_back():
                        # main-activity 라 BACK 거부 → soft restart 로 끊음
                        self._soft_restart(package, main_activity)
                        self.back_count = 0
                        # soft_restart 가 같은 화면 다시 띄우면 위 force-stop
                        # 분기로 자연 진입
                    event_count += 1
                    continue
                # 기존 PAUSE 경로 (AUTH_AUTO_BACKOFF=0)
                self._request_user_input(state)
                if not self._wait_for_resume():
                    self._term_reason = "auth_pause_failed"
                    break
                # after resume, re-capture to avoid stale state
                state = self._capture_screen(event_count)
                if not state:
                    self._term_reason = "capture_failed_after_resume"
                    break

            # 1a. Foreground guard: if current activity belongs to a different app
            # (user pressed back to launcher, or our tap opened another app), relaunch.
            activity = state.get("activity", "") or ""
            if activity and activity != "unknown":
                # Primary match: activity FQN under package namespace
                in_target = (activity == package or activity.startswith(package + "."))
                # Fallback: activity listed in the APK's manifest even if its
                # FQN uses a legacy namespace (e.g. DeskClock's
                # com.android.deskclock.* activities under the
                # com.google.android.deskclock package). The manifest is
                # authoritative — if it declares the activity, it's ours.
                if not in_target:
                    declared_acts = getattr(self, "_declared_activities", None)
                    if declared_acts is None:
                        declared_acts = self._load_declared_activities()
                        self._declared_activities = declared_acts
                    if activity in declared_acts:
                        in_target = True
                if not in_target:
                    out_of_app_count += 1
                    self.trap_stats["out_of_app_relaunch"] += 1
                    logger.warning("[TRAP] Out of target app: activity=%s (expected %s). Relaunching [%d]",
                                    activity, package, out_of_app_count)
                    if out_of_app_count >= 5:
                        logger.error("Gave up: foreground never returned to %s", package)
                        self._term_reason = "out_of_app_giveup"
                        break
                    try:
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "force-stop", package],
                                       capture_output=True, timeout=5)
                        time.sleep(0.5)
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "start", "-n", f"{package}/{main_activity}"],
                                       capture_output=True, timeout=20)
                    except subprocess.TimeoutExpired:
                        logger.warning("[fg-guard] relaunch adb timed out, retrying next iter")
                    time.sleep(2)
                    self.back_count = 0
                    self.stall_count = 0
                    event_count += 1
                    continue
                else:
                    out_of_app_count = 0

            # Detect app crash: views=0 means UI dump failed
            if len(state.get("views", [])) == 0:
                empty_count += 1
                logger.warning("Empty UI dump (%d consecutive)", empty_count)
                if empty_count >= 3:
                    logger.info("App likely crashed, restarting...")
                    try:
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "force-stop", package],
                                       capture_output=True, timeout=5)
                        time.sleep(1)
                        subprocess.run(["adb", "-s", self.device_serial, "shell",
                                        "am", "start", "-n", f"{package}/{main_activity}"],
                                       capture_output=True, timeout=20)
                    except subprocess.TimeoutExpired:
                        logger.warning("[crash-restart] adb timed out")
                    empty_count = 0
                    self.tried_actions.clear()
                    time.sleep(3)
                event_count += 1
                continue
            empty_count = 0

            # B 옵션: Stall detection — 같은 structure_str 6회 연속이면 hard reset.
            # RN/Compose 처럼 native view tree 가 비어있어 unseen score 가 같은 view 만
            # 추천하는 경우 / 인증 폼에서 못 빠져나오는 경우 자동 탈출.
            # 한 run 에 최대 3회만 reset → 무한루프 방지.
            cur_struct_hash = state.get("structure_str", "")
            if cur_struct_hash and cur_struct_hash == prev_struct_hash:
                same_hash_streak += 1
            else:
                same_hash_streak = 0
                prev_struct_hash = cur_struct_hash

            if same_hash_streak >= 6 and stall_resets < 3:
                stall_resets += 1
                logger.warning("[STALL] Same structure_str %d times → hard reset #%d (force-stop + relaunch)",
                               same_hash_streak, stall_resets)
                self.trap_stats["stall_reset"] = self.trap_stats.get("stall_reset", 0) + 1
                try:
                    subprocess.run(["adb", "-s", self.device_serial, "shell",
                                    "am", "force-stop", package],
                                   capture_output=True, timeout=5)
                    time.sleep(1)
                    subprocess.run(["adb", "-s", self.device_serial, "shell",
                                    "am", "start", "-n", f"{package}/{main_activity}"],
                                   capture_output=True, timeout=20)
                except subprocess.TimeoutExpired:
                    logger.warning("[stall-reset] adb timed out")
                # 같은 화면 메모리만 비우고 visited screens 는 유지 (이중 탐색 방지)
                same_hash_streak = 0
                prev_struct_hash = ""
                self.tried_actions.clear()
                time.sleep(3)
                event_count += 1
                continue

            # 3-Level hashing: find canonical screen ID
            fp = self.hasher.compute_fingerprint(
                state.get("views", []),
                state.get("activity", ""),
                state.get("screenshot_path", ""),
            )
            match = self.hasher.find_match(fp)
            if match:
                canonical_id = match
                # Track which level matched for stats
                known = self.hasher.known_fingerprints[match]
                if fp.structural_hash == known.structural_hash:
                    self.hash_stats["l1_matches"] += 1
                elif fp.perceptual_hash and known.perceptual_hash:
                    self.hash_stats["l2_matches"] += 1
                else:
                    self.hash_stats["l3_matches"] += 1
            else:
                canonical_id = f"screen_{len(self.hasher.known_fingerprints):03d}"
                self.hasher.register(canonical_id, fp)
                self.hash_stats["new_screens"] += 1
                logger.info("  NEW screen: %s (activity=%s, elements=%d)",
                            canonical_id, state.get("activity", "?"), fp.widget_count)

            state["canonical_id"] = canonical_id
            state["structure_str"] = fp.structural_hash
            state["state_str"] = canonical_id  # Use canonical as state_str

            # Track visits by canonical ID
            self.visited_structures[canonical_id] += 1
            self.visited_screens.add(canonical_id)

            # P0-10b: 정상 (auth 아닌) 화면 진입 시 auth consecutive 카운터
            # reset — 다음에 auth 만나도 fresh start.
            if hasattr(self, "_auth_consecutive_count") and self._auth_consecutive_count:
                self._auth_consecutive_count.clear()

            # P0-10f (2026-05-05): structure_str 학습 매칭 — 디스크에 저장된
            # 이전 잡들의 차단 화면 구조와 같으면 즉시 BACK + 진입 액션
            # blacklist. 단어/desc 무관 — view tree hash 매칭.
            if state.get("structure_str") in self._learned_structures:
                self.trap_stats["learned_skip"] = self.trap_stats.get("learned_skip", 0) + 1
                if self.action_history:
                    last_desc = (self.action_history[-1].get("event_desc")
                                 or self.action_history[-1].get("desc", ""))
                    if last_desc:
                        self.external_blacklist.add(last_desc)
                logger.info(
                    "[learned] match structure on %s — back (skips=%d)",
                    canonical_id, self.trap_stats["learned_skip"],
                )
                if not self._press_back():
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                event_count += 1
                continue

            # P0-10c (2026-05-05): 영구 blocked canonical 즉시 BACK.
            # 절대 탈출 불가 화면 (KB ARS 결제 외부 페이지 + raon 보안 키패드
            # 같은) 은 한 번 마킹된 후 다시 도달해도 walk 시간 안 쓰게.
            if canonical_id in getattr(self, "_permanent_blocked_canonicals", set()):
                self.trap_stats["permanent_blocked_revisit"] = \
                    self.trap_stats.get("permanent_blocked_revisit", 0) + 1
                # 진입 액션도 blacklist (학습 누적)
                if self.action_history:
                    last_desc = (self.action_history[-1].get("event_desc")
                                 or self.action_history[-1].get("desc", ""))
                    if last_desc:
                        self.external_blacklist.add(last_desc)
                logger.info(
                    "[blocked] revisit %s — back + blacklist (revisits=%d)",
                    canonical_id,
                    self.trap_stats["permanent_blocked_revisit"],
                )
                if not self._press_back():
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                event_count += 1
                continue

            # P0-5 (2026-05-04): must_reach 매칭 — 매 dump 시 fixture spec 과
            # 비교. 활성 activity / view text 가 spec 의 activity_substr /
            # text_substr 에 hit 하면 그 description 을 hit set 에 추가.
            if self.must_reach_specs:
                act = (state.get("activity") or "").lower()
                # view text/desc 다 모아 한 번에 매칭
                view_blob = " ".join(
                    (v.get("text", "") or "") + " " + (v.get("content_desc", "") or "")
                    for v in (state.get("views") or [])
                ).lower()
                for spec in self.must_reach_specs:
                    if spec["description"] in self.must_reach_hit:
                        continue
                    m = spec["match_any"]
                    matched = False
                    for substr in (m.get("activity_substr") or []):
                        if substr and substr.lower() in act:
                            matched = True
                            break
                    if not matched:
                        for substr in (m.get("text_substr") or []):
                            if substr and substr.lower() in view_blob:
                                matched = True
                                break
                    if matched:
                        self.must_reach_hit.add(spec["description"])
                        logger.info(
                            "[coverage] reached must_reach: %s (%d/%d)",
                            spec["description"][:40],
                            len(self.must_reach_hit), len(self.must_reach_specs),
                        )

            # 2026-04-30 Provisional 마킹 (Pass 1 — Stage 3 안 vision 호출 안 함).
            # quality fail 시 (Compose wrapper / dominant WebView / 빈 Flutter
            # 등) state 에 needs_vision_in_revisit flag 만 남기고 그대로 진행.
            # 실제 vision 호출은 Pass 2 (Stage 5 LLM 후 재탐색 phase) 에서.
            # 이유: Stage 3 안 vision 호출은 walker 사이클을 5초+ 막고,
            # ScreenMap context (LLM description) 부재 상태라 Pass 2 의 batch 호출이
            # 더 효율적 (cache hit, 정확도 ↑, 잡 시간 안 늘림).
            # 2026-05-01: external page guard (W1+W2+W3) — webview 가
            # Queens Smile / 카카오 OAuth / 네이버 / 외부 도메인 으로 navigate
            # 하면 그 화면 더 walking 안 하고 BACK + 직전 trigger blacklist.
            try:
                from .outbound_intent_guard import (
                    detect_outbound_intent, append_global_domain,
                )
                is_ext, reason, matched_domain = detect_outbound_intent(
                    state.get("views", []),
                    own_domain_parts=self._own_domain_parts,
                    global_domains=self._global_domains,
                )
                if is_ext:
                    logger.info("[external_guard] %s — back + learn structure", reason)
                    self.trap_stats["external_back"] = self.trap_stats.get("external_back", 0) + 1
                    # A-3 (2026-05-06): 글로벌 도메인 학습 — 앱간 공유.
                    # matched_domain (URL/bare-domain hit) 만 글로벌 등록. W2
                    # 키워드 hit (keyword) 는 domain 정보 없으니 skip.
                    if matched_domain and self._learned_dir:
                        append_global_domain(
                            self._learned_dir, matched_domain, self.package,
                            own_domain_parts=self._own_domain_parts,
                        )
                        self._global_domains.add(matched_domain.lower())
                    # P0-10f: 화면 구조 (view tree hash) 학습 — desc 무관.
                    if state.get("structure_str"):
                        self._learn_structure(state["structure_str"])
                    # 진입 액션은 in-memory blacklist (이번 잡 안에서만)
                    if self.action_history:
                        last_desc = self.action_history[-1].get("event_desc") or self.action_history[-1].get("desc", "")
                        if last_desc:
                            self.external_blacklist.add(last_desc)
                    # P0-1 (2026-05-04): _press_back 가 False (main-activity 거부)
                    # 일 때 무한 재호출되는 dead-lock fix. 7fe3f44a 잡 17:31 부터
                    # 6분+ stall — 같은 external URL 계속 detect → BACK 거부 →
                    # 같은 dump → 반복. Back 안 통하면 soft_restart 로 끊는다.
                    backed = self._press_back()
                    if not backed:
                        # 같은 external URL hit 카운터 — 3회 이상이면 강제 restart
                        ext_hits = getattr(self, "_external_stall_count", 0) + 1
                        self._external_stall_count = ext_hits
                        if ext_hits >= 3:
                            logger.info("[external_guard] %d hits without Back — soft restart", ext_hits)
                            self._soft_restart(package, main_activity)
                            self._external_stall_count = 0
                            self.back_count = 0
                            event_count += 1
                            continue
                    else:
                        self._external_stall_count = 0
                    self.wait_for_stable(timeout=2.0)
                    continue  # walking 계속, paused 안 함
            except Exception as e:
                logger.debug("[external_guard] failed: %s", e)

            if self._view_tree_chain:
                if not self._view_tree_chain.is_primary_sufficient(state.get("views", [])):
                    state["needs_vision_in_revisit"] = True
                    self.trap_stats["provisional_marked"] += 1
                    # capture.py 가 이미 state.json 을 dump 한 후라 디스크에 flag
                    # 안 들어감. main loop 에서 마킹 후 다시 dump — 외부 도구가
                    # state.json 만 보고도 provisional 알 수 있게.
                    try:
                        screen_idx = len(self.states) - 1
                        if screen_idx >= 0:
                            state_json = self.output_dir / "states" / f"state_{screen_idx:04d}.json"
                            if state_json.exists():
                                state_json.write_text(
                                    json.dumps(state, indent=2, ensure_ascii=False),
                                    encoding="utf-8",
                                )
                    except Exception as e:
                        logger.debug("[provisional] state.json re-dump failed: %s", e)

            # Mark activity as visited in the plan (so we don't re-launch it)
            cur_activity = state.get("activity", "") or ""
            if cur_activity:
                self.plan_visited.add(cur_activity)

            # 1b2. Periodic hub refresh — every 25 events, tap a fresh bottom-nav
            # tab to expose the walker to screens that are only reachable via
            # hub rotation (Home/Search/Library/Browse). Without this, greedy
            # top-of-stack walk can stay in one hub (Home) forever.
            if event_count > 0 and event_count % 25 == 0 and not self.stall_count:
                bottom_tabs = [v for v in state.get("views", [])
                               if "bottomnav" in (v.get("class", "") + v.get("parent_class", "")).lower()
                               and v.get("clickable")]
                if len(bottom_tabs) >= 2:
                    tab_idx = (event_count // 25) % len(bottom_tabs)
                    tab = bottom_tabs[tab_idx]
                    logger.info("[hub-refresh] event=%d tab %d/%d desc=%s",
                                event_count, tab_idx + 1, len(bottom_tabs),
                                (tab.get("content_desc") or tab.get("text") or "?")[:30])
                    self._tap_view(tab)
                    event_count += 1
                    self.wait_for_stable(timeout=2.0)  # was time.sleep(0.8)
                    continue

            # 1c. Overlay handling: distinguish popup menu (walk) vs
            #     blocking dialog (dismiss). Popups contain app-specific
            #     tappable items (Settings, Share, …) we want to visit.
            if state.get("is_dialog"):
                views_cur = state.get("views", [])
                is_popup = self._detect_popup_menu(views_cur)
                if is_popup:
                    popup_items = self._popup_items(views_cur)
                    # 2026-04-29: popup item 은 fragment-global. 같은 overflow
                    # 메뉴를 Alarm/Clock/Timer 탭에서 띄워도 popup 자체는 동일
                    # (Settings, Help 등). canonical_id 가 background fragment
                    # 따라 달라져서 tried 매번 reset 되는 게 진짜 'Settings 만
                    # 누름' 의 root cause.
                    untried = [pi for pi in popup_items
                               if self.extractor.get_action_desc(pi) not in self.tried_popup_items]
                    target = untried[0] if untried else None
                    if target:
                        desc = target.get("content_desc") or target.get("text") or target.get("resource_id") or "?"
                        logger.info("[popup] tapping menu item: %s (untried %d/%d, global)",
                                    desc[:40], len(untried), len(popup_items))
                        self.trap_stats["popup_item_tapped"] += 1
                        action_desc = self.extractor.get_action_desc(target)
                        # canonical_id 별 + global 양쪽에 기록. canonical 별은 같은
                        # 화면 안 라운드로빈, global 은 fragment 갈아타도 유지.
                        self.tried_actions[canonical_id].add(action_desc)
                        self.tried_popup_items.add(action_desc)
                        self._tap_view(target)
                        event_count += 1
                        self.wait_for_stable(timeout=2.0)  # was time.sleep(0.8)
                        continue
                    if popup_items:
                        # 모두 시도 — popup_global_tried 가 5 item 다 가지고 있음.
                        # 더 누를 거 없으니 popup 닫고 메인으로. 이후 fragment 갈아타
                        # popup 다시 떠도 untried=[] 라 즉시 dismiss.
                        logger.info("[popup] all %d items tried (global) — dismissing", len(popup_items))
                        self.trap_stats["popup_exhausted"] += 1
                        if self._dismiss_dialog(state):
                            event_count += 1
                            self.wait_for_stable(timeout=1.5)
                            continue
                    # No popup items extractable — dismiss as fallback
                logger.info("[TRAP] Dialog detected on %s — attempting dismiss", canonical_id)
                self.trap_stats["dialog_dismissed"] += 1
                if self._dismiss_dialog(state):
                    event_count += 1
                    self.wait_for_stable(timeout=1.5)  # was time.sleep(0.8)
                    continue

            # 2. Detect stall (same canonical screen 3+ times in a row)
            if canonical_id == self.last_canonical:
                self.stall_count += 1
            else:
                self.stall_count = 0
            self.last_canonical = canonical_id

            if self.stall_count >= 2:
                # 2026-04-30: Vision-LLM fallback BEFORE press back.
                # XML extractor 가 못 잡는 화면 (Compose/WebView/Flutter 또는 score
                # 가중치 누적 stall — TimePicker OK 0회 같은) 에서 화면 보고
                # actionable 좌표 추정. Disabled 면 즉시 False → 기존 back 흐름 유지.
                if self._try_vision_fallback(state, canonical_id):
                    self.stall_count = 0
                    event_count += 1
                    continue
                logger.info("Stall on %s (%d times), pressing back", canonical_id, self.stall_count)
                did_back = self._press_back()
                if not did_back:
                    # Back would exit the app — soft restart instead
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                    self.stall_count = 0
                    event_count += 1
                    continue
                self.back_count += 1
                event_count += 1
                time.sleep(0.5)

                if self.back_count >= 3:
                    # Before soft-restart, try to directly launch a planned activity —
                    # this uses the static ScreenMap as a navigation map, reaching activities
                    # that UI walk alone would never find.
                    if self._try_plan_launch(package):
                        self.back_count = 0
                        self.stall_count = 0
                        event_count += 1
                        continue
                    logger.info("Too many backs — soft-restart for fresh walk")
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                    self.stall_count = 0
                continue

            # 3. Get actionable elements and score them
            actions = self._get_scored_actions(state)

            if not actions:
                # No tap targets available (either real dead-end OR UI dump failed
                # due to persistent animations).  Try navigator launch first — this
                # bypasses UI and reaches declared activities via `am start`.
                if self._try_plan_launch(package):
                    event_count += 1
                    continue
                if not self._press_back():
                    self._soft_restart(package, main_activity)
                    self.back_count = 0
                    event_count += 1
                    continue
                self.back_count += 1
                event_count += 1
                time.sleep(0.5)
                continue

            # If best score is very low but there are untried actions, still try them
            if actions[0]["score"] < -2.0:
                untried = [a for a in actions if a.get("desc", "") not in self.tried_actions.get(canonical_id, set())]
                if untried:
                    actions = untried  # Use untried actions even if scored low
                else:
                    logger.info("All %d actions tried on %s, backing out", len(actions), canonical_id)
                    if not self._press_back():
                        self._soft_restart(package, main_activity)
                        self.back_count = 0
                        event_count += 1
                        continue
                    self.back_count += 1
                    event_count += 1
                    time.sleep(0.5)
                    continue

            # 4. Pick action — diversify among top 3 untried to escape greedy traps.
            # 4a. Scroll budget: drop scroll actions on canonicals that already
            # exhausted their budget (≥SCROLL_CAP swipes) or hit the boundary
            # (Δviews ≤3 after the last swipe). Prevents infinite-scroll feeds
            # from monopolizing walk time. See A3 for boundary detection.
            SCROLL_CAP = 5
            scroll_blocked = (
                canonical_id in self.scrollable_exhausted
                or self.scroll_count_per_screen[canonical_id] >= SCROLL_CAP
            )
            if scroll_blocked:
                n_before = len(actions)
                actions = [a for a in actions if a.get("action") != "scroll"]
                dropped = n_before - len(actions)
                if dropped:
                    self.trap_stats["scroll_cap_blocked"] += dropped
                    logger.info("[TRAP] scroll cap on %s (count=%d, exhausted=%s) — dropped %d scroll actions",
                                canonical_id,
                                self.scroll_count_per_screen[canonical_id],
                                canonical_id in self.scrollable_exhausted,
                                dropped)
                if not actions:
                    # Nothing left to do here — back out
                    self._press_back()
                    self.back_count += 1
                    event_count += 1
                    time.sleep(0.5)
                    continue

            # Pure greedy keeps hitting the same high-scored item when that
            # item's result state gets coalesce'd back to the same canonical.
            # Rotating among the top-3 untried exposes the walker to more
            # branches in Compose apps where many items are close in score.
            # P0-11 (2026-05-06): BFS-style 화면 내부 우선 — 사용자 지적
            # "하단 탭만 누르면서 화면 내부 버튼 무시" 패턴 fix.
            # P1-6 의 R5+ score boost (+3) 가 너무 강해 bottom_nav 가 매번
            # score 9~10, 화면 내부 버튼 (지도 보기 / 상태 텍스트 변경 등)
            # 이 score 5~7 로 압도됨. 그 결과 walk 가 탭만 누르며 같은
            # 종류 화면 변형만 잡고 화면 내 콘텐츠 버튼 도달 X.
            #
            # Fix: 같은 canonical 의 미시도 액션 중 bottom_nav 아닌 것
            # (= 화면 내부 element) 이 있으면 그것을 우선. 화면 내부 다
            # 시도하면 그제서야 탭 click 허용.
            tried_set = self.tried_actions.get(canonical_id, set())
            untried_internal = [
                a for a in actions
                if a.get("desc", "") not in tried_set
                and not str(((a.get("view") or a).get("_list_view_group") or "")).startswith("bottom_nav")
            ]
            if untried_internal:
                # 화면 내부 미시도 — 탭 무시
                untried_top = untried_internal[:5]
            else:
                untried_top = [a for a in actions[:5] if a.get("desc", "") not in tried_set]
                if not untried_top:
                    untried_top = actions[:3]
            # Round-robin by event count so each visit to the same state picks
            # a different top candidate.
            best = untried_top[event_count % len(untried_top)]

            # C (2026-05-03): task-keyword override — task fixture 가 정의한
            # 키워드 (메뉴/매장/장바구니/마이페이지/메가오더 등) 가 view text
            # 또는 content_desc 에 hit 한 미시도 액션이 있으면 score 휴리스틱
            # 무시하고 그것을 best 로 강제. R5/R6/E 같은 score-tuning 으로는
            # 닿지 못한 task path (find_store / view_membership / checkout)
            # 에 walk 를 직접 끌어준다. 미시도 필터가 있어 같은 keyword 화면을
            # 무한히 뱅뱅 돌지 않음.
            if self.task_keywords:
                tried_set = self.tried_actions.get(canonical_id, set())
                keyword_hits: list[dict] = []
                for a in actions:
                    desc = a.get("desc", "")
                    if desc in tried_set:
                        continue
                    # P0-10g (2026-05-05): TASK-KW override 가 external_blacklist
                    # 무시해서 "바로 주문" / "최근주문" 같은 결제 entry 액션을
                    # force-stop 후에도 다시 click 하던 회귀 (b341a3ae) fix.
                    # blacklist desc 는 score 9.00 이라도 skip.
                    if desc in self.external_blacklist:
                        continue
                    v = a.get("view") or a
                    text_blob = f"{v.get('text','') or ''} {v.get('content_desc','') or ''}"
                    if any(kw in text_blob for kw in self.task_keywords):
                        keyword_hits.append(a)
                if keyword_hits:
                    keyword_hits.sort(key=lambda x: -float(x.get("score", 0) or 0))
                    kw_best = keyword_hits[0]
                    if kw_best is not best:
                        logger.info(
                            "[TASK-KW] override on %s — desc=%r score=%.1f (was %.1f)",
                            canonical_id,
                            (kw_best.get("desc") or "")[:40],
                            float(kw_best.get("score", 0) or 0),
                            float(best.get("score", 0) or 0),
                        )
                        best = kw_best

            # P1-3 (2026-05-05): must_reach priority — TASK-KW 보다 더 강한
            # override. 미 hit must_reach spec 의 text_substr 에 매칭되는
            # 액션이 있으면 강제 best. 90d2f770 잡에서 must_reach 2/8 (25%)
            # 만 도달했던 패턴 — task_keyword 는 일반적이고 must_reach 는
            # 잡 KPI 정의이므로 spec 직격이 합리적.
            if self.must_reach_specs:
                tried_set = self.tried_actions.get(canonical_id, set())
                unhit_substrs: list[str] = []
                for spec in self.must_reach_specs:
                    if spec["description"] in self.must_reach_hit:
                        continue
                    unhit_substrs.extend(
                        (spec["match_any"].get("text_substr") or [])
                    )
                if unhit_substrs:
                    mr_hits: list[dict] = []
                    for a in actions:
                        desc = a.get("desc", "")
                        if desc in tried_set:
                            continue
                        # P0-10g: blacklist desc 는 must_reach override 도 skip
                        if desc in self.external_blacklist:
                            continue
                        v = a.get("view") or a
                        text_blob = (
                            (v.get("text", "") or "") + " "
                            + (v.get("content_desc", "") or "")
                        ).lower()
                        if any(s.lower() in text_blob for s in unhit_substrs):
                            mr_hits.append(a)
                    if mr_hits:
                        mr_hits.sort(key=lambda x: -float(x.get("score", 0) or 0))
                        mr_best = mr_hits[0]
                        if mr_best is not best:
                            logger.info(
                                "[MUST-REACH] override on %s — desc=%r score=%.1f (was %.1f)",
                                canonical_id,
                                (mr_best.get("desc") or "")[:40],
                                float(mr_best.get("score", 0) or 0),
                                float(best.get("score", 0) or 0),
                            )
                            best = mr_best

            # 4b. RecyclerView/list trap: cap list-item taps per screen.
            # After 3 item taps on the same canonical, force a scroll-down so
            # we see new content instead of tapping identical-looking items.
            # 2026-04-29: GridView / ViewPager / HorizontalScrollView /
            # Compose LazyColumn 도 동일 처리. Compose 의 LazyColumn 은 native
            # 측에서 ComposeView + 자식들이 List* 클래스 또는 lazy* 시그너로 보임.
            best_view = best.get("view") or best
            parent_cls = str(best_view.get("parent_class", "")).lower()
            list_kw = (
                "recyclerview", "listview", "gridview",
                "viewpager", "horizontalscrollview",
                "lazycolumn", "lazyrow", "lazylist", "lazygrid",  # Compose
                "scrollview",  # Compose 의 ScrollView (XML 의 ScrollView 와 다름)
            )
            is_rv_item = any(kw in parent_cls for kw in list_kw)
            if is_rv_item:
                if self.rv_taps_per_screen[canonical_id] >= 3:
                    logger.info("[TRAP] RV cap on %s — scrolling instead of tapping item", canonical_id)
                    self.trap_stats["rv_cap_scrolled"] += 1
                    self._scroll_down(state)
                    self.scroll_count_per_screen[canonical_id] += 1
                    event_count += 1
                    self.wait_for_stable(timeout=1.5)  # was time.sleep(0.7)
                    continue
                self.rv_taps_per_screen[canonical_id] += 1

            # 4c. Scroll budget counter — count direct "scroll" actions too
            # (RV trap path above handles its own _scroll_down counter increment).
            if best.get("action") == "scroll":
                self.scroll_count_per_screen[canonical_id] += 1
            logger.info("Event %d: %s on %s (score=%.2f, visits=%d)",
                        event_count, best["action"], best.get("desc", "?"),
                        best["score"], self.visited_structures[canonical_id])

            # 5. Execute action + record as tried
            prev_canonical = canonical_id
            self.tried_actions[canonical_id].add(best.get("desc", ""))
            # 2026-04-30: per-list_view visit count — best 가 list_view 항목이면
            # 같은 그룹의 N번째 클릭 score 가 다음 iteration 부터 감점됨
            lg = best.get("list_view_group_id")
            if lg:
                self.list_view_visit_count[lg] += 1
                self.trap_stats["list_view_taps"] += 1
            self._execute_action(best, state)
            event_count += 1
            self.wait_for_stable(timeout=2.0)  # was time.sleep(0.7) — Faster walk

            # 5.5. Package guard — if the action sent us into a different app
            # (Gmail via "Send feedback", Chrome via "Privacy policy",
            # share-sheet, etc.), bounce back to the target package so the
            # next iteration's unseen scoring works on our own UI, not
            # someone else's. Disabled via ALLOW_EXTERNAL=1.
            if not self._allow_external:
                self._check_app_bounds()

            # 6. Capture new state, compute its canonical ID, record transition
            new_screen = self._capture_screen(event_count)
            if new_screen:
                # Hash the new state to get its canonical ID
                new_fp = self.hasher.compute_fingerprint(
                    new_screen.get("views", []),
                    new_screen.get("activity", ""),
                    new_screen.get("screenshot_path", ""),
                )
                new_match = self.hasher.find_match(new_fp)
                if new_match:
                    new_canonical = new_match
                else:
                    new_canonical = f"screen_{len(self.hasher.known_fingerprints):03d}"
                    self.hasher.register(new_canonical, new_fp)
                    self.hash_stats["new_screens"] += 1
                    logger.info("  -> NEW screen: %s", new_canonical)

                new_screen["canonical_id"] = new_canonical
                new_screen["state_str"] = new_canonical

                if new_canonical != prev_canonical:
                    self.transitions.append({
                        "from_screen": prev_canonical,
                        "to_screen": new_canonical,
                        "event_type": best["action"],
                        "event_str": best.get("desc", ""),
                    })
                    # R6 (2026-05-03): action 의 target 다이버시티 누적.
                    # 같은 element click 이 매번 다른 화면으로 가면 random transition.
                    self.action_target_diversity[
                        (prev_canonical, best.get("desc", ""))
                    ].add(new_canonical)

        # Save results
        elapsed = time.time() - start_time
        logger.info("Walk done: %d events, %d unique screens, %.0fs",
                     event_count, len(self.visited_structures), elapsed)
        # Trap summary (one line per kind, zero counts omitted)
        trap_summary = {k: v for k, v in self.trap_stats.items() if v}
        if trap_summary:
            logger.info("Traps hit: %s", trap_summary)

        # Final pass: probe every still-declared activity via am start -W so
        # coverage reflects reachability, not just UI-walked screens.
        try:
            self._manifest_scan(package)
        except Exception as e:
            logger.warning("Manifest scan failed: %s", e)

        # Second pass via deep links — many gated activities reject `-n` but
        # accept a VIEW intent with their registered scheme. This recovers
        # 5-15 extra activities for apps with rich intent_filter declarations
        # (Spotify, TikTok, YouTube, etc).
        try:
            self._deep_link_scan(package)
        except Exception as e:
            logger.warning("Deep link scan failed: %s", e)

        return self._save_results(package, elapsed, event_count)

    def _get_scored_actions(self, state: dict) -> list[dict]:
        """Score actions using framework-specific Extractor.

        Delegates to self.extractor.is_actionable() + score_action().
        """
        views = state.get("views", [])
        canonical = state.get("canonical_id", state.get("structure_str", ""))
        visit_count = self.visited_structures.get(canonical, 0)
        tried = self.tried_actions.get(canonical, set())

        # ListView detect — 같은 화면에서 한 번만 (cache 가능하지만 화면 짧은
        # 시간 살아있으니 매번 재계산해도 OK).
        from .list_view_detector import detect_list_views
        list_views = detect_list_views(views)
        # group_id 를 canonical 과 결합해 cross-screen 충돌 방지
        list_views_scoped = [
            {**g, "group_id": f"{canonical}::{g['group_id']}"}
            for g in list_views
        ]

        context = {
            "canonical": canonical,
            "visit_count": visit_count,
            "tried_actions": tried,
            "list_views": list_views_scoped,
            "list_view_visit_count": self.list_view_visit_count,
            # 2026-05-01: external page guard — 외부 도메인 진입 trigger 영구 blacklist
            "external_blacklist": self.external_blacklist,
            # 2026-05-03 (R6): multi-target action diversity — score_action 이
            # 같은 element 가 ≥3 다른 화면으로 가면 페널티 적용. 이벤트 hub bias 차단.
            "action_target_diversity": self.action_target_diversity,
            # E (2026-05-03): task fixture 키워드 — view text/desc 매칭 시 보너스.
            "task_keywords": self.task_keywords,
        }

        actions = []
        seen_labels = set()

        for view_idx, view in enumerate(views):
            if not self.extractor.is_actionable(view):
                continue

            # Coalescelicate by label (text > desc > rid > bounds for WebView)
            label = (view.get("text") or view.get("content_desc")
                     or view.get("resource_id") or str(view.get("bounds", "")))
            if label in seen_labels:
                continue
            seen_labels.add(label)

            # view_index 는 list_view 페널티 계산에 필요
            context["view_index"] = view_idx
            score = self.extractor.score_action(view, context)
            action_desc = self.extractor.get_action_desc(view)
            if view.get("clickable"):
                action_type = "click"
            elif view.get("long_clickable"):
                action_type = "longclick"
            elif view.get("scrollable"):
                action_type = "scroll"
            else:
                action_type = "click"

            # list_view 그룹 멤버십 — best 선택 시 visit count 증가에 사용
            from .list_view_detector import view_to_group
            grp = view_to_group(list_views_scoped, view_idx)
            list_view_group_id = grp["group_id"] if grp else None

            actions.append({
                "action": action_type,
                "view": view,
                "list_view_group_id": list_view_group_id,
                "score": score,
                "desc": action_desc,
                "bounds": view.get("bounds", {}),
            })

        # Add ViewPager horizontal swipe as a separate synthetic action per pager
        # found on screen. Many apps use ViewPager(2) for tab pagination and
        # bottom-nav isn't the only way — some swipe-only screens are invisible
        # to tap-only walk.
        pagers_seen = set()
        for view in views:
            cls = str(view.get("class", "") or "")
            parent = str(view.get("parent_class", "") or "")
            is_pager = (
                "ViewPager" in cls or "ViewPager2" in cls
                or "HorizontalScroll" in cls
            )
            if not is_pager:
                continue
            # coalescee by bounds so a pager containing child pagers doesn't double-count
            b = str(view.get("bounds", ""))
            if b in pagers_seen:
                continue
            pagers_seen.add(b)
            desc = f"swipe_horizontal pager@{b[:24]}"
            if desc in tried:
                continue
            actions.append({
                "action": "swipe_horizontal",
                "view": view,
                "score": 2.0,  # moderate priority, below untried taps
                "desc": desc,
                "bounds": view.get("bounds", {}),
            })

        actions.sort(key=lambda a: a["score"], reverse=True)
        return actions


    def _pick_sample_input(self, view: dict) -> str:
        """P0-7: EditText 의 rid/hint/label 에 맞는 fixture sample_inputs 룩업.
        없으면 task_keywords 첫 단어, 그것도 없으면 빈 문자열."""
        rid = (view.get("resource_id", "") or "").lower()
        hint = (view.get("text", "") or view.get("content_desc", "") or "").lower()
        # fixture.sample_inputs: {"search": "메뉴", "id": "test", "phone": "01012345678"} 형식
        samples = (self._fixture or {}).get("sample_inputs") or {}
        for key, val in samples.items():
            k = key.lower()
            if k and (k in rid or k in hint):
                return str(val)
        # fallback: task_keywords 첫 단어 (메뉴/매장 등)
        if self.task_keywords:
            return self.task_keywords[0]
        return "test"

    def _learn_structure(self, structure_str: str) -> None:
        """P0-10f: 외부/auth/결제 화면의 view tree hash 를 디스크 저장.
        다음 잡 시작 시 자동 로드 → 같은 구조 화면 도달 시 즉시 BACK.

        단어/desc 매칭 X (P0-10d/e 폐기 이유). view tree 구조만 — 같은
        화면이면 잡 마다 동일 hash.
        """
        if not structure_str or structure_str in self._learned_structures:
            return
        self._learned_structures.add(structure_str)
        try:
            if self._learned_path:
                self._learned_path.write_text(
                    json.dumps({
                        "package": self.package,
                        "blocked_structures": sorted(self._learned_structures),
                        "updated_at": time.time(),
                    }, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info("[learned] +structure %s... → %d total saved",
                            structure_str[:16], len(self._learned_structures))
        except Exception as e:
            logger.debug("[learned] save failed: %s", e)

    def _execute_action(self, action: dict, state: dict) -> None:
        """Execute a UI action via ADB."""
        import subprocess

        bounds = action.get("bounds", {})
        if not bounds:
            view = action.get("view", {})
            bounds = view.get("bounds", {})

        if isinstance(bounds, dict):
            x = (bounds.get("x1", 0) + bounds.get("x2", 0)) // 2
            y = (bounds.get("y1", 0) + bounds.get("y2", 0)) // 2
        elif isinstance(bounds, list) and len(bounds) == 2:
            x = (bounds[0][0] + bounds[1][0]) // 2
            y = (bounds[0][1] + bounds[1][1]) // 2
        elif isinstance(bounds, str):
            # Parse "[x1,y1][x2,y2]" format
            import re
            nums = re.findall(r'\d+', bounds)
            if len(nums) >= 4:
                x = (int(nums[0]) + int(nums[2])) // 2
                y = (int(nums[1]) + int(nums[3])) // 2
            else:
                return
        else:
            return

        if action["action"] == "click":
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "tap", str(x), str(y)],
                           capture_output=True, timeout=5)
            # P0-7 (2026-05-04): EditText 클릭 시 키보드 뜨는데 텍스트 입력 0
            # 이라 검색/로그인 후속 화면 도달 못 함. fixture 의 sample_inputs
            # 또는 task_keywords 첫 단어로 자동 입력.
            view = action.get("view") or {}
            cls = (view.get("class", "") or "").lower()
            if "edittext" in cls:
                sample = self._pick_sample_input(view)
                if sample:
                    time.sleep(0.4)  # 키보드 뜰 시간
                    if self._input_text(sample):
                        logger.info("[input_text] EditText filled: %r", sample[:30])
                        # input 후 IME enter — 검색 form 의 submit trigger
                        try:
                            subprocess.run(
                                ["adb", "-s", self.device_serial, "shell",
                                 "input", "keyevent", "66"],   # KEYCODE_ENTER
                                capture_output=True, timeout=5,
                            )
                        except Exception:
                            pass
        elif action["action"] == "longclick":
            # 700ms press — reliably triggers long-press handlers
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x), str(y), str(x), str(y), "700"],
                           capture_output=True, timeout=5)
        elif action["action"] == "scroll":
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x), str(y), str(x), str(y - 300), "300"],
                           capture_output=True, timeout=5)
        elif action["action"] == "swipe_horizontal":
            # ViewPager page-swipe. Direction based on last swipe to alternate.
            vw = action.get("view", {}) or {}
            b = vw.get("bounds", "") or bounds
            if isinstance(b, str):
                import re
                nums = re.findall(r'\d+', b)
                if len(nums) >= 4:
                    x1_px, x2_px = int(nums[0]), int(nums[2])
                    w = x2_px - x1_px
                else:
                    w = 800
            else:
                w = 800
            # Swipe from 80% width → 20% (right-to-left = next page)
            x_start = x + int(w * 0.3)
            x_end = x - int(w * 0.3)
            subprocess.run(["adb", "-s", self.device_serial, "shell",
                            "input", "swipe", str(x_start), str(y), str(x_end), str(y), "250"],
                           capture_output=True, timeout=5)

    _DISMISS_KEYWORDS = (
        "dismiss", "close", "cancel", "×", "x", "no", "skip",
        "닫기", "취소", "아니오", "나중에", "skip",
        "확인",  # rarely means close but often is primary action
    )

    def _dismiss_dialog(self, state: dict) -> bool:
        """Try to close an overlay dialog by clicking the most likely dismiss button.

        Priority:
          1. Button with "close"/"cancel"/"dismiss" text or content-desc
          2. ImageView at top-right (typical close X)
          3. Fallback: press back
        """
        views = state.get("views", [])

        # 1. Text-based match
        for v in views:
            if not v.get("clickable"):
                continue
            text = (v.get("text") or "").lower().strip()
            desc = (v.get("content_desc") or "").lower().strip()
            combined = text + " " + desc
            if any(kw in combined for kw in self._DISMISS_KEYWORDS):
                logger.info("Dismiss via button: %r", text or desc)
                self._tap_view(v)
                return True

        # 2. Top-right ImageView (common close X position)
        top_right_candidates = [
            v for v in views
            if v.get("clickable")
            and "imageview" in (v.get("class") or "").lower()
            and self._is_top_right(v.get("bounds"))
        ]
        if top_right_candidates:
            v = top_right_candidates[0]
            logger.info("Dismiss via top-right image: bounds=%s", v.get("bounds"))
            self._tap_view(v)
            return True

        # 3. Back fallback
        logger.info("Dismiss via Back key")
        return self._press_back()

    @staticmethod
    def _is_top_right(bounds) -> bool:
        return view_tree_parser.is_top_right(bounds)

    def _scroll_down(self, state: dict, distance: int = 800) -> None:
        """Swipe up to reveal more content below (scrolls the list down).

        Uses the center x-axis of the first scrollable view, or screen center
        as fallback.  Distance defaults to 800px which is ~1/3 of a standard screen.

        Boundary detection (A3): after the swipe settles, do a light dump and
        compare node count vs the pre-scroll state. If Δ ≤ 3 the scrollable
        has nothing new to give — record canonical in `scrollable_exhausted`
        so future iterations stop trying to scroll this screen.
        """
        scrollable = next(
            (v for v in state.get("views", []) if v.get("scrollable")),
            None,
        )
        # Default: middle of screen
        x, y_start, y_end = 540, 1600, 1600 - distance
        if scrollable:
            import re
            b = scrollable.get("bounds", "")
            nums = re.findall(r"\d+", str(b))
            if len(nums) >= 4:
                x1, y1, x2, y2 = (int(n) for n in nums[:4])
                x = (x1 + x2) // 2
                y_start = y1 + (y2 - y1) * 3 // 4
                y_end = max(y1 + 40, y_start - distance)
        subprocess.run(
            ["adb", "-s", self.device_serial, "shell",
             "input", "swipe", str(x), str(y_start), str(x), str(y_end), "300"],
            capture_output=True, timeout=5,
        )

        # A3: boundary detection — only if we know the canonical we're on
        # and it's not already marked exhausted (avoid extra dumps).
        canonical = getattr(self, "last_canonical", "") or ""
        if not canonical or canonical in self.scrollable_exhausted:
            return
        try:
            time.sleep(0.5)
            from . import u2_helper
            xml_after = u2_helper.dump_hierarchy(self.device_serial, timeout=3.0)
            if not xml_after or "<hierarchy" not in xml_after:
                return
            n_after = xml_after.count("<node")
            n_before = len(state.get("views", []))
            delta = abs(n_after - n_before)
            if delta <= 3:
                self.scrollable_exhausted.add(canonical)
                self.trap_stats["scroll_boundary_reached"] += 1
                logger.info("[scroll] boundary reached on %s (Δnodes=%d, before=%d after=%d)",
                            canonical, delta, n_before, n_after)
        except Exception as e:
            logger.debug("[scroll] boundary detection skipped: %s", e)

    def _scroll_right(self, state: dict, distance: int = 600) -> None:
        """Horizontal swipe (right→left) to reveal next page in a horizontal
        scroller — ViewPager / HorizontalScrollView / Compose Pager / image
        carousels. Mirror of `_scroll_down` along the x-axis.

        Picks the first horizontally-scrollable container by class hint, then
        falls back to any `scrollable=true` view, then to screen center.
        Distance 600px is ~half a standard width.
        """
        views = state.get("views", []) or []
        # Class hints first (more accurate than the generic scrollable flag,
        # which often marks vertical RecyclerViews too).
        h_class_kw = ("viewpager", "horizontalscroll", "horizontalscrollview",
                      "horizontalpager", "horizontalrecyclerview")
        target = None
        for v in views:
            cls = (v.get("class") or "").lower()
            if any(kw in cls for kw in h_class_kw):
                target = v
                break
        if target is None:
            target = next((v for v in views if v.get("scrollable")), None)

        # Default: middle of screen, swipe right→left
        y, x_start, x_end = 1200, 900, 900 - distance
        if target:
            import re
            b = target.get("bounds", "")
            nums = re.findall(r"\d+", str(b))
            if len(nums) >= 4:
                x1, y1, x2, y2 = (int(n) for n in nums[:4])
                y = (y1 + y2) // 2
                x_start = x1 + (x2 - x1) * 3 // 4
                x_end = max(x1 + 40, x_start - distance)
        subprocess.run(
            ["adb", "-s", self.device_serial, "shell",
             "input", "swipe", str(x_start), str(y), str(x_end), str(y), "300"],
            capture_output=True, timeout=5,
        )

        # Same boundary detection as vertical scroll.
        canonical = getattr(self, "last_canonical", "") or ""
        if not canonical or canonical in self.scrollable_exhausted:
            return
        try:
            time.sleep(0.5)
            from . import u2_helper
            xml_after = u2_helper.dump_hierarchy(self.device_serial, timeout=3.0)
            if not xml_after or "<hierarchy" not in xml_after:
                return
            n_after = xml_after.count("<node")
            n_before = len(views)
            if abs(n_after - n_before) <= 3:
                self.scrollable_exhausted.add(canonical)
                self.trap_stats["scroll_boundary_reached"] += 1
                logger.info("[scroll-right] boundary on %s (before=%d after=%d)",
                            canonical, n_before, n_after)
        except Exception as e:
            logger.debug("[scroll-right] boundary detection skipped: %s", e)

    def _try_vision_fallback(self, state: dict, canonical_id: str) -> bool:
        """Vision LLM 으로 actionable element 추출 + tap. 성공 시 True.

        호출 조건 (호출자 책임): stall_count >= 2 또는 명시적 trigger.
        실패 케이스 → False:
          - vision_tapper 비활성 (env 안 켜졌거나 API key 없음)
          - 현재 state 에 screenshot 없음
          - extract_actionable 가 빈 리스트 반환 (budget/저신뢰/네트워크 실패 etc.)
          - 모든 후보가 이미 시도됨 (canonical_id 안에서 tried 누적)
        """
        if self.vision_tapper is None:
            return False
        screenshot_path = state.get("screenshot_path") or ""
        if not screenshot_path or not Path(screenshot_path).exists():
            return False

        try:
            actions = self.vision_tapper.extract_actionable(
                screenshot_path, state_str=canonical_id,
            )
        except Exception as e:
            logger.warning("[vision-fallback] extract failed: %s", e)
            return False

        if not actions:
            return False

        tried = self.tried_actions.setdefault(canonical_id, set())
        for action in actions:
            label = action.get("label", "?")
            bounds = action.get("bounds")
            action_key = f"vision:{label}@{bounds}"
            if action_key in tried:
                continue
            try:
                cx, cy = self.vision_tapper.click_point(action)
            except Exception as e:
                logger.warning("[vision-fallback] click_point failed: %s", e)
                tried.add(action_key)
                continue
            logger.info(
                "[vision-fallback] tap %r at (%d,%d) conf=%.2f — %s",
                label, cx, cy, action.get("confidence", 0),
                (action.get("expected_outcome") or "")[:60],
            )
            try:
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "input", "tap", str(cx), str(cy)],
                    capture_output=True, timeout=5,
                )
            except Exception as e:
                logger.warning("[vision-fallback] adb tap failed: %s", e)
                tried.add(action_key)
                return False
            tried.add(action_key)
            self.trap_stats["vision_fallback"] = self.trap_stats.get("vision_fallback", 0) + 1
            time.sleep(0.5)
            return True

        return False

    def _tap_view(self, view: dict) -> None:
        """Tap the center of a view's bounds."""
        import re
        bounds = view.get("bounds", "")
        if isinstance(bounds, str):
            nums = re.findall(r"\d+", bounds)
            if len(nums) >= 4:
                cx = (int(nums[0]) + int(nums[2])) // 2
                cy = (int(nums[1]) + int(nums[3])) // 2
                subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "input", "tap", str(cx), str(cy)],
                    capture_output=True, timeout=5,
                )
                time.sleep(0.3)

    def _bootstrap_navigation(self, package: str, main_activity: str) -> None:
        """One-time routine that fires BEFORE the main walk loop.

        Strategy (minimal, conservative — don't burn too many events):
          1. Capture initial state
          2. If BottomNavigationView exists — tap each tab once
          3. If DrawerLayout exists — open drawer via edge-swipe; the main loop
             will then see the drawer's items as newly actionable elements
          4. If Toolbar with action icons exists — tap the top-right icon (usually
             overflow / profile / settings entry)

        Does NOT record these as transitions yet; main loop will capture resulting
        states naturally.
        """
        state = self._capture_screen(-1)
        if not state:
            return
        views = state.get("views", [])

        # --- 2. Bottom navigation ---
        # 2026-04-29: detection 강화. 이전엔 'bottomnav' class 매칭만 했는데
        # DeskClock 의 tab views 가 cls=FrameLayout, rid=tab_menu_alarm 패턴 —
        # bottomnav 키워드 없어서 발견 0건이었음. Evidence: workspace/b6b5abed
        # state_-001.json 의 4개 tab_menu_* 가 모두 매칭 실패.
        def _is_bottom_tab(v: dict) -> bool:
            if not v.get("clickable"):
                return False
            cls = v.get("class", "")
            parent = v.get("parent_class", "")
            rid = v.get("resource_id", "") or ""
            haystack = (cls + parent).lower()
            if "bottomnav" in haystack or "bottomtab" in haystack:
                return True
            # rid 패턴 — Material/Compose 의 tab_menu_*, nav_*, tab_*
            rl = rid.lower()
            if rl.startswith(("tab_menu_", "nav_tab_", "bottom_tab_")):
                return True
            # bounds — 화면 하단 (y > 80% of 2400) 의 가로 작은 영역
            bounds_str = v.get("bounds", "")
            try:
                # "[x1,y1][x2,y2]" 형식
                import re
                m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
                if m:
                    x1, y1, x2, y2 = map(int, m.groups())
                    if y1 >= 1900 and (x2 - x1) < 300:  # 하단 영역 + 가로 짧음
                        return True
            except Exception:
                pass
            return False

        bottom_tabs = [v for v in views if _is_bottom_tab(v)]
        for tab in bottom_tabs[:5]:  # cap to 5
            tab_label = tab.get("content_desc") or tab.get("text") or "?"
            logger.info("[bootstrap] tapping bottom-nav: %s", tab_label)
            self._tap_view(tab)
            self.wait_for_stable(timeout=2.0)

            # 2026-04-29 Cycle 2 Fix A: 각 tab 진입 후 fragment 안 fab 시도.
            # Evidence (46e287b6): ALARMS fragment 13 state 모두 'Add alarm'
            # fab Button 있는데 walker 가 한 번도 안 누름. bootstrap 의
            # 메인 화면 fab 만 시도해서 BEDTIME fab 만 7번 누르고 끝남.
            # 각 tab 진입 후 그 fragment 의 fab 1개 누르면 TimePicker /
            # AlarmEditor / TimerKeypad 같은 핵심 task 화면 캡처 가능.
            try:
                cur_screen = self._capture_screen(-1)
                if cur_screen:
                    cur_views = cur_screen.get("views", []) or []
                    cur_fabs = self._find_fab_views(cur_views)
                    if cur_fabs:
                        fab = cur_fabs[0]
                        fab_desc = fab.get("content_desc") or fab.get("text") or "?"
                        logger.info("[bootstrap] tapping FAB in tab '%s': %s",
                                    tab_label, fab_desc)
                        self._tap_view(fab)
                        self.wait_for_stable(timeout=2.0)
                        # 결과 화면 (TimePicker dialog 등) 다음 사이클에서
                        # capture 하도록 back. 단 dialog 가 떠있으면 back =
                        # cancel 동작이라 dialog 닫힘 — 다음 main loop 가 다시
                        # 발견 가능하게.
                        self._press_back()
                        self.wait_for_stable(timeout=1.5)
            except Exception as e:
                logger.debug("[bootstrap] fab-in-tab probe failed: %s", e)

            self._go_home_tab(bottom_tabs)  # return to first tab after each probe
            time.sleep(0.5)

        # --- 3. Drawer ---
        has_drawer = any("drawerlayout" in (v.get("class", "") or "").lower()
                         for v in views)
        if has_drawer or self._has_drawer_toggle(views):
            self._open_drawer()
            self.wait_for_stable(timeout=2.0)  # was time.sleep(0.8)
            # Main loop will discover drawer items as new actionable views

        # --- 4. Top-right toolbar icons (overflow / profile / settings) ---
        top_right = self._find_top_right_actions(views)
        for v in top_right[:2]:  # cap to 2 to avoid burning events
            desc = v.get("content_desc") or v.get("text") or ""
            logger.info("[bootstrap] tapping top-right icon: %s", desc)
            self._tap_view(v)
            self.wait_for_stable(timeout=2.0)  # was time.sleep(0.8)
            # Back to main screen so the loop starts from a known baseline
            self._press_back()
            time.sleep(0.4)

        # --- 5. FAB (Floating Action Button) — 우측하단 + 클릭 시 새 화면/시트 진입 흔함
        # 2026-04-29 추가: DeskClock + 버튼, 알람 추가, 메시지 작성 같은 진입점.
        fabs = self._find_fab_views(views)
        for v in fabs[:1]:  # 보통 FAB 는 화면당 1개
            desc = v.get("content_desc") or v.get("text") or "?"
            logger.info("[bootstrap] tapping FAB: %s", desc)
            self._tap_view(v)
            self.wait_for_stable(timeout=2.0)  # was time.sleep(0.9)
            self._press_back()  # back 으로 주 화면 복귀 (다이얼로그 닫힘)
            time.sleep(0.4)

    def _find_fab_views(self, views: list[dict]) -> list[dict]:
        """FloatingActionButton 후보 — class 매칭 OR 우측하단 영역의 clickable 둥근 버튼."""
        candidates: list[dict] = []
        # 1) 명시적 class: FloatingActionButton (Material) / ExtendedFloatingActionButton
        for v in views:
            if not v.get("clickable"):
                continue
            cls = (v.get("class") or "").lower()
            if "floatingactionbutton" in cls or cls.endswith(".fab"):
                candidates.append(v)
        if candidates:
            return candidates
        # 2) Compose / RN: FAB 가 generic class 라 위치 휴리스틱 — 우측하단 (x>2/3, y>2/3)
        # 화면 크기 기본 1080x2400 가정 — 정확한 viewport 모르므로 bounds 비율로
        for v in views:
            if not v.get("clickable"):
                continue
            bounds = v.get("bounds", "")
            # bounds 형식: "[x1,y1][x2,y2]"
            import re as _re
            m = _re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
            w, h = x2 - x1, y2 - y1
            # 우측 60% 이후 + 하단 65% 이후 + 정사각형 비슷 (가로:세로 0.7~1.3) + 합리적 크기 (50~250px)
            if cx > 600 and cy > 1500 and 50 < w < 300 and 50 < h < 300:
                ratio = w / h if h else 0
                if 0.6 < ratio < 1.4:
                    candidates.append(v)
        return candidates

    def _has_drawer_toggle(self, views: list[dict]) -> bool:
        """Look for hamburger / drawer toggle button."""
        for v in views:
            cls = (v.get("class") or "").lower()
            desc = (v.get("content_desc") or "").lower()
            rid = (v.get("resource_id") or "").lower()
            if not v.get("clickable"):
                continue
            if "open drawer" in desc or "navigation" in desc or "menu" in desc:
                return True
            if "drawer_toggle" in rid or "drawer_indicator" in rid or "menu_icon" in rid:
                return True
            # Material toolbar up-button
            if "actionbar$tab" in cls or "draweractionbardrawertoggle" in cls:
                return True
        return False

    def _open_drawer(self) -> None:
        """Open navigation drawer via left-edge swipe.  Safe to call even if no drawer."""
        # Swipe from just inside the left edge (x=20) to mid-screen, slower duration
        # so Android's edge detector accepts it.
        subprocess.run(
            ["adb", "-s", self.device_serial, "shell",
             "input", "swipe", "20", "720", "600", "720", "400"],
            capture_output=True, timeout=5,
        )

    def _go_home_tab(self, tabs: list[dict]) -> None:
        """Return to the first bottom-nav tab (usually Home)."""
        if tabs:
            self._tap_view(tabs[0])

    def _find_top_right_actions(self, views: list[dict]) -> list[dict]:
        """Top-right area Toolbar menu items — commonly search/settings/profile."""
        import re
        out = []
        for v in views:
            if not v.get("clickable"):
                continue
            cls = (v.get("class") or "").lower()
            if not any(k in cls for k in ("actionmenuitemview", "imagebutton", "imageview")):
                continue
            bounds = v.get("bounds", "")
            nums = re.findall(r"\d+", str(bounds))
            if len(nums) < 4:
                continue
            x1, y1, x2, y2 = (int(n) for n in nums[:4])
            # Top-right: y < 300, x2 > screen_width * 0.6
            if y1 < 300 and x2 > 700:
                out.append(v)
        # Sort right-to-left (furthest right first — often most important)
        out.sort(key=lambda v: -int(re.findall(r"\d+", str(v.get("bounds","")))[2]) if re.findall(r"\d+", str(v.get("bounds",""))) else 0)
        return out

    def _load_plan(self, package: str) -> None:
        """Load declared-but-unvisited activities from the static wireframe ScreenMap.

        Priority:
          1. statically_reachable=True (DEX transitions pointed to them)
          2. has intent_filters (externally invokable) — also extract deep link URIs
          3. rest of declared
        """
        import json as _json
        # wireframe ScreenMap lives one level up from dynamic/
        screenmap_path = self.output_dir.parent / "output" / "screen_map.json"
        if not screenmap_path.exists():
            return
        try:
            screenmap = _json.loads(screenmap_path.read_text(encoding="utf-8"))
        except Exception:
            return
        nodes = screenmap.get("screen_map", {}).get("graph", {}).get("nodes", [])

        reachable, with_filter, rest = [], [], []
        self.plan_deep_links: list[str] = []
        for n in nodes:
            act = n.get("activity", "")
            if not act or not act.startswith(package):
                continue
            # Extract deep link URIs from intent_filters.
            # androguard returns data as a list of separate dicts, each with one
            # of {scheme, host, pathPrefix, ...}.  We combine all within the same
            # filter to form candidate URIs (cartesian product of schemes × hosts).
            for f in n.get("intent_filters", []) or []:
                if not isinstance(f, dict):
                    continue
                if "android.intent.action.VIEW" not in (f.get("actions") or []):
                    continue
                data = f.get("data") or []
                schemes: list[str] = []
                hosts: list[str] = []
                path_prefixes: list[str] = []
                for d in data:
                    if not isinstance(d, dict):
                        continue
                    if d.get("scheme"): schemes.append(d["scheme"])
                    if d.get("host"): hosts.append(d["host"])
                    if d.get("pathPrefix"): path_prefixes.append(d["pathPrefix"])
                # Skip http/https generic ones (too broad, usually open in browser)
                app_schemes = [s for s in schemes if s not in ("http", "https")]
                if not app_schemes and schemes:
                    app_schemes = schemes  # fallback
                hosts_or_stub = hosts or ["app"]
                prefix = path_prefixes[0] if path_prefixes else ""
                for sch in app_schemes:
                    for host in hosts_or_stub:
                        uri = f"{sch}://{host}{prefix or ''}"
                        if uri not in self.plan_deep_links:
                            self.plan_deep_links.append(uri)
            if n.get("status") not in (None, "declared"):
                continue
            if n.get("statically_reachable"):
                reachable.append(act)
            elif n.get("intent_filters"):
                with_filter.append(act)
            else:
                rest.append(act)
        # Cap total to avoid burning events on dead code
        self.plan_targets = (reachable + with_filter + rest)[:30]
        # Cap deep links similarly
        self.plan_deep_links = self.plan_deep_links[:15]

    def _try_plan_launch(self, package: str) -> bool:
        """Explicitly launch the next unvisited activity OR deep link.

        Alternates between direct Activity launch and Deep link invocation to
        cover both internal screens and deep-link-only entry points.

        Returns True if we actually kicked off something.
        """
        # First: try a deep link (these reach screens that direct launch can't)
        if getattr(self, "plan_deep_links", None):
            uri = self.plan_deep_links.pop(0)
            logger.info("[plan] Deep-link launching: %s", uri)
            try:
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "start", "-a", "android.intent.action.VIEW",
                     "-d", uri, package],
                    capture_output=True, timeout=10,
                )
                out = ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", errors="replace")
                if "Error" not in out and r.returncode == 0:
                    self.trap_stats["deeplink_launched"] += 1
                    time.sleep(2.0)
                    return True
                logger.info("[plan]   deep link failed: %s", out.strip()[:80])
            except Exception as e:
                logger.info("[plan]   deep link exception: %s", e)

        # Then: direct Activity launch
        if not self.plan_targets:
            return False
        for target in list(self.plan_targets):
            if target in self.plan_visited or target in self.plan_failed:
                self.plan_targets.remove(target)
                continue
            logger.info("[plan] Direct-launching: %s", target)
            self.plan_targets.remove(target)
            try:
                r = subprocess.run(
                    ["adb", "-s", self.device_serial, "shell",
                     "am", "start", "-n", f"{package}/{target}"],
                    capture_output=True, timeout=10,
                )
                out = (r.stdout or b"").decode("utf-8", errors="replace") \
                    + (r.stderr or b"").decode("utf-8", errors="replace")
                if "Error" in out or r.returncode != 0:
                    logger.info("[plan]   launch failed: %s", out.strip()[:100])
                    self.plan_failed.add(target)
                    continue
                self.trap_stats["plan_launched"] += 1
                time.sleep(2.0)
                return True
            except Exception as e:
                logger.info("[plan]   launch exception: %s", e)
                self.plan_failed.add(target)
        return False




    def _save_results(self, package: str, elapsed: float, event_count: int) -> dict:
        """Save walk results in DroidBot-compatible format."""
        # P0-5 (2026-05-04): termination_reason 분류 — 이슈 1 (무한루프 검증)
        # 의 진단 데이터. P0-5b (2026-05-05): break 경로별 self._term_reason
        # 우선, 없으면 while 조건으로 fallback.
        explicit = getattr(self, "_term_reason", None)
        if explicit:
            term_reason = explicit
        elif self._cancel_flag.exists():
            term_reason = "cancelled"
        elif self.must_reach_specs and \
                len(self.must_reach_hit) / len(self.must_reach_specs) >= self.coverage_target:
            term_reason = "coverage_target_reached"
        elif event_count >= self.max_events:
            term_reason = "max_events"
        elif elapsed >= self.timeout:
            term_reason = "timeout"
        else:
            term_reason = "unknown"

        result = {
            "states": self.states,
            "transitions": self.transitions,
            "activities_found": list({s["activity"] for s in self.states if s.get("activity")}),
            "stats": {
                "total_events": event_count,
                "unique_screens": len(self.hasher.known_fingerprints),
                "unique_screens_raw": len(self.visited_screens),
                "elapsed_seconds": round(elapsed, 1),
                "package": package,
                # P0-5: 분당 events / 화면 발견 속도 — 무한루프 패턴 감지용
                "events_per_minute": round(event_count / max(elapsed, 1) * 60, 1),
                "screens_per_minute": round(
                    self.hash_stats["new_screens"] / max(elapsed, 1) * 60, 2,
                ),
                "termination_reason": term_reason,
                # P0-10: auth wall 회피 카운터 — backoff 가 작동했으면 walk
                # 가 멈추지 않고 다른 path 탐색했다는 신호. 0 이면 auth 안 만남
                # 또는 AUTH_AUTO_BACKOFF=0 모드.
                "auth_backoff_count": self.trap_stats.get("auth_backoff", 0),
                "auth_screens_seen": sorted(getattr(self, "_auth_screens_seen", set())),
                "permanent_blocked_canonicals": sorted(
                    getattr(self, "_permanent_blocked_canonicals", set())
                ),
                "permanent_blocked_revisits": self.trap_stats.get(
                    "permanent_blocked_revisit", 0,
                ),
                # P0-10f: structure-based learning
                "learned_structures_loaded": len(self._learned_structures) - self.trap_stats.get("learned_added_this_tour", 0),
                "learned_structures_total": len(self._learned_structures),
                "learned_skip_count": self.trap_stats.get("learned_skip", 0),
                "must_reach": {
                    "total": len(self.must_reach_specs),
                    "hit": sorted(self.must_reach_hit),
                    "missing": sorted(
                        s["description"] for s in self.must_reach_specs
                        if s["description"] not in self.must_reach_hit
                    ),
                    "ratio": (
                        round(len(self.must_reach_hit) / len(self.must_reach_specs), 3)
                        if self.must_reach_specs else None
                    ),
                    "target": self.coverage_target,
                },
                "hashing": {
                    "l1_structural_matches": self.hash_stats["l1_matches"],
                    "l2_phash_matches": self.hash_stats["l2_matches"],
                    "l3_gnn_matches": self.hash_stats["l3_matches"],
                    "new_screens_discovered": self.hash_stats["new_screens"],
                    "coalesce_ratio": round(
                        1 - len(self.hasher.known_fingerprints) / max(len(self.states), 1), 2
                    ),
                },
            },
        }

        # Save walk.json (compatible with utg_parser output)
        out_path = self.output_dir / "walk.json"
        out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

        return result
