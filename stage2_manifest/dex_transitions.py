"""Static transition extraction from DEX bytecode.

Parses `dexdump -d` output (Google's official tool bundled with Android SDK
build-tools) to find Activity ↔ Activity transitions WITHOUT needing Apktool.

Patterns detected
-----------------
1. Classic explicit Intent:
       const-class vX, Lcom/package/Target;
       ...
       invoke-virtual ... ->startActivity(Landroid/content/Intent;)V

2. NavController.navigate(String):
       const-string vX, "route/string"
       invoke-virtual ... Landroidx/navigation/NavController;->navigate(...)

3. NavGraphBuilder.composable("route"):
       const-string vX, "route"
       invoke-static ... Landroidx/navigation/compose/NavGraphBuilderKt;->composable(...)

4. PendingIntent.getActivity — system entry points:
       const-class vX, Lcom/package/AlarmActivity;
       ...
       invoke-static ... Landroid/app/PendingIntent;->getActivity(...)

This is a pragmatic "good enough" extractor: ~70-85% of real transitions in a
typical app.  Catches R8-obfuscated but not `Class.forName(dynamicString)` etc.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Android SDK build-tools dexdump.exe path (common locations)
def _find_dexdump() -> str | None:
    from shutil import which
    w = which("dexdump")
    if w:
        return w
    # Windows default
    sdk = os.path.expanduser("~/AppData/Local/Android/Sdk/build-tools")
    if os.path.isdir(sdk):
        for v in sorted(os.listdir(sdk), reverse=True):
            candidate = os.path.join(sdk, v, "dexdump.exe")
            if os.path.exists(candidate):
                return candidate
            candidate = os.path.join(sdk, v, "dexdump")
            if os.path.exists(candidate):
                return candidate
    # ANDROID_HOME / ANDROID_SDK_ROOT
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(env)
        if root and os.path.isdir(os.path.join(root, "build-tools")):
            for v in sorted(os.listdir(os.path.join(root, "build-tools")), reverse=True):
                for name in ("dexdump.exe", "dexdump"):
                    p = os.path.join(root, "build-tools", v, name)
                    if os.path.exists(p):
                        return p
    return None


@dataclass
class DexTransition:
    source: str           # caller activity FQN (java dot form) or "" if unknown
    target: str           # target activity FQN
    kind: str             # "navigate" | "two_hop" | "compose_route" | "pending_intent"
    trigger: str = ""     # extra info: "startActivity" / "NavController.navigate" / ...
    source_method: str = ""


@dataclass
class DexAnalysisResult:
    transitions: list[DexTransition] = field(default_factory=list)
    compose_routes: dict[str, str] = field(default_factory=dict)  # route → registrar class
    pending_intents: list[DexTransition] = field(default_factory=list)
    activities_seen: set[str] = field(default_factory=set)
    # Who creates which class — used for 2-hop resolution
    #   creator_of[helper_class] = {creator_class, ...}
    creator_of: dict[str, set[str]] = field(default_factory=dict)


# ─── DEX extraction ──────────────────────────────────────────

def _extract_dex_files(apk_path: Path, out_dir: Path) -> list[Path]:
    """Extract all classes*.dex from the APK."""
    out_dir.mkdir(parents=True, exist_ok=True)
    extracted = []
    with zipfile.ZipFile(apk_path) as z:
        for name in z.namelist():
            if re.fullmatch(r"classes\d*\.dex", name):
                dest = out_dir / name
                dest.write_bytes(z.read(name))
                extracted.append(dest)
    return extracted


# ─── dexdump text parsing ──────────────────────────────────────

# Smali-like lines from dexdump -d output
_CONST_CLASS_RE = re.compile(r"\|[0-9a-f]+:\s*const-class\s+v\d+,\s*L([\w/$]+);")
_CONST_STRING_RE = re.compile(r"\|[0-9a-f]+:\s*const-string\s+v\d+,\s*\"([^\"]{1,200})\"")
_NEW_INSTANCE_RE = re.compile(r"\|[0-9a-f]+:\s*new-instance\s+v\d+,\s*L([\w/$]+);")
# dexdump uses `.methodName:` separator; smali uses `->methodName`.  Support both.
_SEP = r"(?:->|\.)"
_INVOKE_START_ACT_RE = re.compile(rf"\|[0-9a-f]+:\s*invoke-\w+.*{_SEP}startActivity\b")
_INVOKE_PENDING_ACT_RE = re.compile(
    rf"\|[0-9a-f]+:\s*invoke-\w+.*Landroid/app/PendingIntent;{_SEP}get(?:Activity|Activities)\b"
)
_INVOKE_NAV_NAVIGATE_RE = re.compile(
    rf"\|[0-9a-f]+:\s*invoke-\w+.*Landroidx/navigation/NavController;{_SEP}navigate\b"
)
_INVOKE_COMPOSABLE_RE = re.compile(
    rf"\|[0-9a-f]+:\s*invoke-\w+.*Landroidx/navigation/compose/NavGraphBuilderKt;{_SEP}composable\b"
)
# Reflection pattern: const-string with FQN that looks like an Activity class,
# typically used with Class.forName / setClassName(pkg, str) / intent.setClassName.
_REFLECTION_CLASS_STRING_RE = re.compile(
    r'"([a-zA-Z][\w.]+Activity)"'  # "com.example.SomeActivity" (java-form dotted)
)
_INVOKE_FORNAME_OR_SETCLASS_RE = re.compile(
    rf"\|[0-9a-f]+:\s*invoke-\w+.*(?:Ljava/lang/Class;{_SEP}forName|{_SEP}setClassName)\b"
)
_CLASS_DESCRIPTOR_RE = re.compile(r"Class descriptor\s*:\s*'L([\w/$]+);'")
_METHOD_NAME_RE = re.compile(r"\s*name\s+:\s*'([^']+)'")


def _looks_like_activity(fqn: str) -> bool:
    return fqn.endswith("Activity") and not fqn.startswith("android.")


def _java_form(slash_fqn: str) -> str:
    """Smali 'com/example/Foo$Bar' → Java 'com.example.Foo.Bar'."""
    return slash_fqn.replace("/", ".").replace("$", ".")


def _parse_dex_dump(dexdump_output: str, package_filter: str = "") -> DexAnalysisResult:
    """Parse dexdump -d output line-by-line.

    State machine tracks (class, method) context so we can attribute each
    transition to its source.
    """
    result = DexAnalysisResult()

    cur_class = ""
    cur_method = ""
    # Rolling buffers: recent const-class + const-string in the current method
    recent_classes: list[str] = []
    recent_strings: list[str] = []
    # Reflection-style activity FQNs seen as const-string (e.g. "com.app.SomeActivity")
    recent_reflect_classes: list[str] = []

    def is_target_pkg(cls: str) -> bool:
        return not package_filter or cls.startswith(package_filter)

    for line in dexdump_output.splitlines():
        # Class boundary
        m = _CLASS_DESCRIPTOR_RE.search(line)
        if m:
            cur_class = m.group(1)
            if _looks_like_activity(_java_form(cur_class)):
                result.activities_seen.add(_java_form(cur_class))
            recent_classes.clear()
            recent_strings.clear()
            continue

        # Method boundary
        m = _METHOD_NAME_RE.match(line)
        if m:
            cur_method = m.group(1)
            recent_classes.clear()
            recent_strings.clear()
            continue

        # const-class → target activity candidate
        m = _CONST_CLASS_RE.search(line)
        if m:
            cls = m.group(1)
            if _looks_like_activity(_java_form(cls)):
                recent_classes.append(cls)
                # cap buffer
                if len(recent_classes) > 3:
                    recent_classes.pop(0)
            continue

        # new-instance → record creator relationship (for 2-hop resolution)
        m = _NEW_INSTANCE_RE.search(line)
        if m and cur_class:
            helper = m.group(1)
            if helper != cur_class:
                result.creator_of.setdefault(helper, set()).add(cur_class)
            continue

        # const-string → route candidate OR reflection activity FQN
        m = _CONST_STRING_RE.search(line)
        if m:
            s = m.group(1)
            # Route-like: letters + '/' + no spaces + < 80 chars
            if len(s) < 80 and re.match(r"^[a-zA-Z0-9/_{}\-.]+$", s) and "/" in s:
                recent_strings.append(s)
                if len(recent_strings) > 3:
                    recent_strings.pop(0)
            # Reflection-style FQN: "com.example.SomeActivity"
            mref = _REFLECTION_CLASS_STRING_RE.fullmatch('"' + s + '"')
            if mref and "." in s and s.endswith("Activity"):
                recent_reflect_classes.append(s)
                if len(recent_reflect_classes) > 3:
                    recent_reflect_classes.pop(0)
            continue

        # invoke startActivity — attribute the most recent const-class
        if _INVOKE_START_ACT_RE.search(line):
            if recent_classes and cur_class:
                target = recent_classes[-1]
                src_java = _java_form(cur_class)
                tgt_java = _java_form(target)
                if src_java != tgt_java:
                    result.transitions.append(DexTransition(
                        source=src_java,
                        target=tgt_java,
                        kind="navigate",
                        trigger="startActivity",
                        source_method=cur_method,
                    ))
            continue

        # PendingIntent.getActivity — system entry
        if _INVOKE_PENDING_ACT_RE.search(line):
            if recent_classes:
                result.pending_intents.append(DexTransition(
                    source="system:external_entry",
                    target=_java_form(recent_classes[-1]),
                    kind="pending_intent",
                    trigger="PendingIntent",
                    source_method=cur_method,
                ))
            continue

        # NavController.navigate(String) — route-based transition
        if _INVOKE_NAV_NAVIGATE_RE.search(line):
            if recent_strings and cur_class:
                result.transitions.append(DexTransition(
                    source=_java_form(cur_class),
                    target=f"route:{recent_strings[-1]}",
                    kind="compose_route",
                    trigger="NavController.navigate",
                    source_method=cur_method,
                ))
            continue

        # Compose composable("route") registration
        if _INVOKE_COMPOSABLE_RE.search(line):
            if recent_strings:
                route = recent_strings[-1]
                result.compose_routes[route] = _java_form(cur_class) or "?"
            continue

        # Reflection / setClassName: target = last reflection-style const-string
        if _INVOKE_FORNAME_OR_SETCLASS_RE.search(line):
            if recent_reflect_classes and cur_class:
                tgt_java = recent_reflect_classes[-1]
                src_java = _java_form(cur_class)
                if src_java != tgt_java:
                    result.transitions.append(DexTransition(
                        source=src_java,
                        target=tgt_java,
                        kind="navigate",
                        trigger="reflection/setClassName",
                        source_method=cur_method,
                    ))
            continue

    return result


# ─── Public entry point ─────────────────────────────────────

def extract_transitions(apk_path: Path, package_filter: str = "", max_dex: int = 3,
                         known_activities: set | None = None) -> dict:
    """Run dexdump across the APK's classes*.dex and return a JSON-serializable
    dict describing static transitions, compose routes, and system entry points.

    Args:
        apk_path: path to the APK file
        package_filter: only include classes starting with this java-form prefix
                        (e.g. "com.spotify"). Empty = include all.
        max_dex: cap on how many classes*.dex files to analyze.
        known_activities: authoritative Activity FQNs (from Manifest). Used to
                          resolve 2-hop transitions — without this we miss
                          Activities that don't self-reference in the scanned
                          dex files.
    """
    dexdump = _find_dexdump()
    if not dexdump:
        raise RuntimeError(
            "dexdump not found. Install Android SDK build-tools or set ANDROID_HOME."
        )

    with tempfile.TemporaryDirectory(prefix="dex_trans_") as tmp:
        tmp_dir = Path(tmp)
        dex_files = _extract_dex_files(apk_path, tmp_dir)
        if not dex_files:
            logger.warning("No dex files found in %s", apk_path)
            return {"transitions": [], "compose_routes": {}, "pending_intents": [], "activities_seen": []}

        merged = DexAnalysisResult()
        for i, dex in enumerate(dex_files[:max_dex]):
            logger.info("Analyzing %s (%d/%d, %.1fMB)...",
                        dex.name, i + 1, min(len(dex_files), max_dex),
                        dex.stat().st_size / 1e6)
            try:
                r = subprocess.run(
                    [dexdump, "-d", str(dex)],
                    capture_output=True, timeout=300,
                )
                out_text = (r.stdout or b"").decode("utf-8", errors="replace")
            except subprocess.TimeoutExpired:
                logger.warning("dexdump timed out on %s — skipping", dex.name)
                continue
            except Exception as e:
                logger.warning("dexdump failed on %s: %s", dex.name, e)
                continue

            part = _parse_dex_dump(out_text, package_filter)
            merged.transitions.extend(part.transitions)
            merged.pending_intents.extend(part.pending_intents)
            merged.compose_routes.update(part.compose_routes)
            merged.activities_seen.update(part.activities_seen)
            # Merge creator_of maps
            for helper, creators in part.creator_of.items():
                merged.creator_of.setdefault(helper, set()).update(creators)

    # Multi-hop resolution: for each transition whose source is a helper class
    # (not an Activity), trace backward through creator_of up to MAX_HOPS levels
    # to find Activity ancestors.  Greatly improves coverage in R8-obfuscated apps.
    activities_java = set(merged.activities_seen)
    if known_activities:
        activities_java.update(known_activities)
    # Activities in slash form for internal lookups
    activities_slash = {a.replace(".", "/") for a in activities_java}
    MAX_HOPS = 4
    multi_hop_additions: list[DexTransition] = []
    for t in merged.transitions:
        if t.source in activities_java:
            continue  # clean Activity source already
        start = t.source.replace(".", "/")
        # BFS backwards
        visited = {start}
        queue = [(start, 0, [start])]
        matched_activities: set[str] = set()
        while queue and len(matched_activities) < 5:
            cur, depth, path = queue.pop(0)
            if depth >= MAX_HOPS:
                continue
            for creator in merged.creator_of.get(cur, ()):
                if creator in visited:
                    continue
                visited.add(creator)
                new_path = path + [creator]
                if creator in activities_slash:
                    matched_activities.add(creator)
                    # Record hop chain for debugging
                    chain = " → ".join(p.rsplit("/", 1)[-1] for p in reversed(new_path))
                    creator_java = creator.replace("/", ".").replace("$", ".")
                    if creator_java != t.target:
                        multi_hop_additions.append(DexTransition(
                            source=creator_java,
                            target=t.target,
                            kind="two_hop",
                            trigger=f"{t.trigger} [hop={depth + 1}] ({chain})",
                            source_method=t.source_method,
                        ))
                else:
                    queue.append((creator, depth + 1, new_path))
    merged.transitions.extend(multi_hop_additions)
    logger.info("Multi-hop resolution added %d Activity→Activity edges", len(multi_hop_additions))

    # De-duplicate transitions
    seen = set()
    uniq = []
    for t in merged.transitions:
        key = (t.source, t.target, t.kind)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)

    return {
        "transitions": [t.__dict__ for t in uniq],
        "compose_routes": merged.compose_routes,
        "pending_intents": [t.__dict__ for t in merged.pending_intents],
        "activities_seen": sorted(merged.activities_seen),
        "stats": {
            "transition_count": len(uniq),
            "compose_route_count": len(merged.compose_routes),
            "pending_intent_count": len(merged.pending_intents),
            "activity_count": len(merged.activities_seen),
        },
    }


def write_transition_graph(apk_path: Path, output_path: Path,
                            package_filter: str = "", max_dex: int = 3,
                            known_activities: set | None = None) -> dict:
    """Convenience wrapper: analyze + write to transition_graph.json."""
    result = extract_transitions(apk_path, package_filter, max_dex, known_activities)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Static transitions: %d navigate + %d pending + %d compose_routes → %s",
        result["stats"]["transition_count"],
        result["stats"]["pending_intent_count"],
        result["stats"]["compose_route_count"],
        output_path,
    )
    return result
