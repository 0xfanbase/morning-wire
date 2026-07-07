"""PROTECTED CORE. render.py's VALID_* enums and the pipeline's pinned
retention/threshold constants are frozen contracts: render.sanitize_digest
silently DROPS any item whose type/priority/jurisdiction/status/
verification.level isn't in the current VALID_* set. A silent rename of an
enum value (e.g. a refactor that renames "peer_move" to "industry_move"
without updating every writer) makes every item using the old value vanish
from the public page with no error anywhere.

Compares the live values against audit/enum-snapshot.json, a frozen copy a
human must deliberately update in a reviewed PR. Any drift is CRITICAL until
that update happens -- this check cannot know whether a changed value is an
intentional improvement or an accidental rename, so it always treats drift
as needing a human decision, never auto-updates its own snapshot.
"""
import json

from base import finding, could_not_run

CHECK_ID = "enum_constant_freeze"
MODE = "hard"

SNAPSHOT_PATH = "audit/enum-snapshot.json"


def _current_values(repo_root):
    import sys
    sys.path.insert(0, str(repo_root / "scripts"))
    import render as render_mod
    import fetch as fetch_mod
    import run as run_mod
    import heal as heal_mod
    import registers as registers_mod

    return {
        "VALID_TYPES": sorted(render_mod.VALID_TYPES),
        "VALID_PRIORITIES": sorted(render_mod.VALID_PRIORITIES),
        "VALID_JURISDICTIONS": sorted(render_mod.VALID_JURISDICTIONS),
        "VALID_STATUSES": sorted(render_mod.VALID_STATUSES),
        "VALID_VERIFY_LEVELS": sorted(render_mod.VALID_VERIFY_LEVELS),
        "VALID_HEALTH_STATUSES": sorted(render_mod.VALID_HEALTH_STATUSES),
        "DIGEST_ITEMS_MAX_AGE_DAYS": run_mod.DIGEST_ITEMS_MAX_AGE_DAYS,
        "SEEN_ITEMS_MAX_AGE_DAYS": run_mod.SEEN_ITEMS_MAX_AGE_DAYS,
        "MAX_ITEM_AGE_DAYS": fetch_mod.MAX_ITEM_AGE_DAYS,
        "MAX_CHURN_FRACTION": registers_mod.MAX_CHURN_FRACTION,
        "MAX_CHURN_FLOOR": registers_mod.MAX_CHURN_FLOOR,
        "FAILURE_THRESHOLD": heal_mod.FAILURE_THRESHOLD,
    }


def run(repo_root):
    snap_path = repo_root / SNAPSHOT_PATH
    if not snap_path.exists():
        return [could_not_run(CHECK_ID, f"{SNAPSHOT_PATH} is missing -- cannot compare against a baseline")]

    try:
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"{SNAPSHOT_PATH} is not valid JSON: {exc}")]

    try:
        current = _current_values(repo_root)
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import pipeline modules to read live values: {exc}")]

    findings = []
    for key, expected in snapshot.items():
        actual = current.get(key)
        if actual != expected:
            findings.append(finding(
                CHECK_ID, "critical",
                f"'{key}' drifted from its frozen snapshot",
                f"Frozen snapshot ({SNAPSHOT_PATH}) says {expected!r}, but the live code has {actual!r}. "
                "If this is an intentional change, update the snapshot in a reviewed, human-authored PR "
                "-- this check will not do it automatically, since it cannot tell an intentional change "
                "from an accidental rename that would silently drop conforming items.",
                {"key": key, "snapshot_value": expected, "live_value": actual},
            ))
    for key in current:
        if key not in snapshot:
            findings.append(finding(
                CHECK_ID, "warn", f"'{key}' exists in code but not in the frozen snapshot",
                f"Live value is {current[key]!r}; add it to {SNAPSHOT_PATH} in a reviewed PR.", {"key": key},
            ))
    return findings
