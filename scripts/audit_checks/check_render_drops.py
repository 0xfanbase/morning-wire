"""PROTECTED CORE. render.sanitize_digest() is the sole gate before anything
reaches the public page: it silently DROPS (never raises on) any item that
fails schema validation. A drop is invisible unless someone counts -- this
check makes the count and the reason for every drop explicit.

Purely read-only: sanitize_digest is a pure function over a dict; this never
calls render.render() and never touches docs/index.html or docs/feed.xml.

Counts occurrences per id, not just set membership (see audit/lessons.md
L5): if two items happen to share an id and one fails validation while the
other survives, a plain `ids_in - ids_out` set difference sees the id
present in both sides and reports nothing, even though one whole item
silently vanished. Comparing per-id counts closes that gap.
"""
import json
from collections import Counter

from base import finding, could_not_run

CHECK_ID = "render_drops"
MODE = "hard"


def run(repo_root):
    try:
        import sys
        sys.path.insert(0, str(repo_root / "scripts"))
        import render as render_mod
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import scripts/render.py: {exc}")]

    try:
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/digest.json: {exc}")]

    items_in = digest.get("items") or []
    counts_in = Counter(it.get("id") for it in items_in if isinstance(it, dict))
    try:
        clean = render_mod.sanitize_digest(digest)
    except Exception as exc:
        return [finding(CHECK_ID, "critical", "sanitize_digest raised instead of dropping",
                         f"sanitize_digest() itself raised: {exc}. It is documented to drop malformed "
                         "items, never crash -- this means some item shape can wedge the daily publish.",
                         {})]

    counts_out = Counter(it.get("id") for it in clean.get("items", []))
    # An id is "dropped" if it's missing entirely OR present fewer times than
    # it appeared in the input (the duplicate-id case).
    dropped_ids = sorted(id_ for id_, n_in in counts_in.items() if counts_out.get(id_, 0) < n_in)
    if not dropped_ids:
        return []

    dropped_set = set(dropped_ids)
    dropped_titles = [it.get("title", "?")[:60] for it in items_in if it.get("id") in dropped_set]
    return [finding(
        CHECK_ID, "critical",
        f"{len(dropped_ids)} item(s) would be silently dropped by sanitize_digest",
        f"Ids {dropped_ids} fail schema validation (or a duplicate-id copy of them does) and would "
        f"never reach the public page: {dropped_titles}. Read scripts/render.py's _valid_item to find "
        "which clause failed.",
        {"dropped_ids": dropped_ids},
    )]
