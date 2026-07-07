"""PROTECTED CORE. render.sanitize_digest() is the sole gate before anything
reaches the public page: it silently DROPS (never raises on) any item that
fails schema validation. A drop is invisible unless someone counts -- this
check makes the count and the reason for every drop explicit.

Purely read-only: sanitize_digest is a pure function over a dict; this never
calls render.render() and never touches docs/index.html or docs/feed.xml.
"""
import json

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
    ids_in = {it.get("id") for it in items_in if isinstance(it, dict)}
    try:
        clean = render_mod.sanitize_digest(digest)
    except Exception as exc:
        return [finding(CHECK_ID, "critical", "sanitize_digest raised instead of dropping",
                         f"sanitize_digest() itself raised: {exc}. It is documented to drop malformed "
                         "items, never crash -- this means some item shape can wedge the daily publish.",
                         {})]

    ids_out = {it.get("id") for it in clean.get("items", [])}
    dropped_ids = ids_in - ids_out
    if not dropped_ids:
        return []

    dropped_titles = [it.get("title", "?")[:60] for it in items_in if it.get("id") in dropped_ids]
    return [finding(
        CHECK_ID, "critical",
        f"{len(dropped_ids)} item(s) would be silently dropped by sanitize_digest",
        f"Ids {sorted(dropped_ids)} fail schema validation and would never reach the public page: "
        f"{dropped_titles}. Read scripts/render.py's _valid_item to find which clause failed.",
        {"dropped_ids": sorted(dropped_ids)},
    )]
