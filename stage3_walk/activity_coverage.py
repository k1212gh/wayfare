"""Track activity coverage between static analysis and dynamic walk."""

import logging

logger = logging.getLogger(__name__)


def check_coverage(walk: dict, static_activities: list[str]) -> dict:
    """Compare dynamically discovered activities against static analysis.

    FIX #13: Uses suffix matching to handle relative vs full activity names.
    e.g., ".MainActivity" matches "com.example.app.MainActivity"
    """
    discovered = set(walk.get("activities_found", []))
    expected = set(static_activities)

    if not expected:
        return {
            "ratio": 1.0 if discovered else 0.0,
            "discovered": sorted(discovered),
            "expected": [],
            "covered": [],
            "missed": [],
            "extra": sorted(discovered),
        }

    # Build normalized matching: full name → short name
    covered = set()
    for exp_act in expected:
        if exp_act in discovered:
            covered.add(exp_act)
        else:
            # Suffix match: ".MainActivity" matches "com.example.app.MainActivity"
            exp_short = exp_act.rsplit(".", 1)[-1] if "." in exp_act else exp_act
            for disc_act in discovered:
                disc_short = disc_act.rsplit(".", 1)[-1] if "." in disc_act else disc_act
                if exp_short == disc_short or disc_act.endswith(exp_act) or exp_act.endswith(disc_act):
                    covered.add(exp_act)
                    break

    missed = expected - covered
    extra = discovered - {a for a in discovered if any(
        a == e or a.endswith(e.rsplit(".", 1)[-1]) for e in expected
    )}

    ratio = len(covered) / len(expected) if expected else 0.0

    report = {
        "ratio": ratio,
        "discovered_count": len(discovered),
        "expected_count": len(expected),
        "covered_count": len(covered),
        "missed_count": len(missed),
        "covered": sorted(covered),
        "missed": sorted(missed),
        "extra": sorted(extra),
    }

    logger.info(
        "Coverage: %d/%d (%.0f%%), missed: %d, extra: %d",
        len(covered), len(expected), ratio * 100,
        len(missed), len(extra),
    )

    if missed:
        logger.info("Missed activities: %s", ", ".join(sorted(missed)[:5]))

    return report
