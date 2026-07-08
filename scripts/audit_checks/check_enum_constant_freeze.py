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
import re

from base import finding, could_not_run

CHECK_ID = "enum_constant_freeze"
MODE = "hard"

SNAPSHOT_PATH = "audit/enum-snapshot.json"


def _page_html_type_keys(repo_root):
    """render.py's VALID_TYPES is not the only copy of this enum: summarise.py
    keeps an independent VALID_TYPES to validate the model's own output
    (drifting from render.py's would let it coerce a real type to "news", or
    accept a value render.py would then silently drop at render time), and
    scripts/templates/page.html's TYPE_LABEL/BUCKETS maps every type to a
    display label/category client-side (a type present in data but missing
    from either map renders with no label / lands in no category, silently,
    since page.html has no equivalent of render.py's schema gate). page.html
    is JS-in-HTML, not an importable module, so these are extracted with a
    narrow, human-reviewable regex rather than a real JS parser -- good
    enough to freeze a snapshot against, not a general JS-object reader."""
    text = (repo_root / "scripts" / "templates" / "page.html").read_text(encoding="utf-8")

    label_block = re.search(r"const TYPE_LABEL\s*=\s*\{(.*?)\};", text, re.DOTALL)
    label_keys = sorted(re.findall(r"(\w+):\s*[\"']", label_block.group(1))) if label_block else []

    buckets_block = re.search(r"const BUCKETS\s*=\s*\{(.*?)\n\};", text, re.DOTALL)
    bucket_types = set()
    if buckets_block:
        for arr in re.findall(r"types:\s*\[(.*?)\]", buckets_block.group(1)):
            bucket_types.update(re.findall(r"[\"'](\w+)[\"']", arr))

    return sorted(label_keys), sorted(bucket_types)


def _page_html_juris_maps(repo_root):
    """A jurisdiction code's MEANING -- its display order and its full name --
    lives only in page.html's JURIS_ORDER/JURIS_FULL, client-side, with no
    render.py-side gate. VALID_JURISDICTIONS (frozen above) only freezes the
    set of valid CODES; it says nothing about what "UK" is displayed as or
    where it sorts. Silently reordering JURIS_ORDER or relabeling a
    JURIS_FULL entry (e.g. "HK": "United States") would be a real, high-impact
    bug on this project's central editorial focus (jurisdiction correctness)
    with zero warning anywhere -- see audit/lessons.md L5. Same narrow-regex
    tradeoff as _page_html_type_keys above."""
    text = (repo_root / "scripts" / "templates" / "page.html").read_text(encoding="utf-8")

    order_m = re.search(r"const JURIS_ORDER\s*=\s*\[(.*?)\];", text)
    order = re.findall(r"[\"'](\w+)[\"']", order_m.group(1)) if order_m else []

    full_m = re.search(r"const JURIS_FULL\s*=\s*\{(.*?)\};", text, re.DOTALL)
    full = dict(re.findall(r"(\w+):\s*[\"']([^\"']*)[\"']", full_m.group(1))) if full_m else {}

    return order, full


def _current_values(repo_root):
    import sys
    sys.path.insert(0, str(repo_root / "scripts"))
    import render as render_mod
    import fetch as fetch_mod
    import run as run_mod
    import heal as heal_mod
    import registers as registers_mod
    import summarise as summarise_mod

    page_labels, page_bucket_types = _page_html_type_keys(repo_root)
    page_juris_order, page_juris_full = _page_html_juris_maps(repo_root)

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
        # Independent copies of the same VALID_TYPES contract -- see the
        # docstring above for why each one matters.
        "SUMMARISE_VALID_TYPES": sorted(summarise_mod.VALID_TYPES),
        "PAGE_HTML_TYPE_LABEL_KEYS": page_labels,
        "PAGE_HTML_BUCKET_TYPES": page_bucket_types,
        "PAGE_HTML_JURIS_ORDER": page_juris_order,
        "PAGE_HTML_JURIS_FULL": page_juris_full,
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
