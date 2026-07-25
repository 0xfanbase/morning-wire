"""Red/green fixtures for two render-layer page-blanking bugs found by the
2026-07-24 duo external audit:

1. Radar date validation was shape-only: the `^\\d{4}-\\d{2}-\\d{2}$` regex
   accepted "2026-08-32", which then threw client-side inside the shared
   Intl.DateTimeFormat helper and aborted the whole render -- feed, briefs
   and radar all blank, no error shown. sanitize_digest must drop a
   calendar-invalid date and keep a real one. Sibling gap: a
   source_health[].checked_at value never went through _normalize_iso the
   way every other date field does, so a malformed value reached the same
   client-side formatter; it must be normalized or stripped.

2. Placeholder substitution ran as SEQUENTIAL whole-document .replace()
   calls: __DIGEST_JSON__ first, then __OG_TITLE__/__OG_DESC__ -- so an
   item title containing a literal "__OG_DESC__" got substituted INSIDE the
   already-embedded JSON payload with HTML-escaped prose (html.escape does
   not escape backslashes, so a top_of_mind backslash could make the payload
   genuinely invalid JSON and kill the <script> boot). Substitution must be
   single-pass: replaced output is never itself a substitution target.

Built from the real committed digest (like test_l5_robustness_gaps.py) with
render output redirected to a scratch directory so docs/ is never touched.

Run manually: python3 scripts/audit_checks/fixtures/test_render_hardening.py
Exits non-zero if any assertion fails.
"""
import copy
import json
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_real_digest():
    return json.loads((REPO_ROOT / "data" / "digest.json").read_text(encoding="utf-8"))


def test_radar_calendar_validation():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import render as render_mod

    digest = _load_real_digest()
    test_digest = {**digest, "radar": [
        {"date": "2026-08-32", "label": "fixture: impossible day", "jurisdiction": "US"},
        {"date": "2026-13-01", "label": "fixture: impossible month", "jurisdiction": "US"},
        {"date": "2099-02-28", "label": "fixture: valid future date", "jurisdiction": "US"},
    ]}
    clean = render_mod.sanitize_digest(copy.deepcopy(test_digest))
    labels = [r["label"] for r in clean["radar"]]
    if any("impossible" in lbl for lbl in labels):
        return False, f"RED FIXTURE FAILED: a calendar-invalid radar date survived sanitize_digest: {labels}"
    if "fixture: valid future date" not in labels:
        return False, f"GREEN FIXTURE FAILED: a valid future radar date was dropped: {labels}"
    return True, None


def test_checked_at_normalized_or_stripped():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import render as render_mod

    digest = _load_real_digest()
    test_digest = {**digest, "source_health": [
        {"name": "Fixture Bad Clock", "status": "ok", "note": "", "checked_at": "not-a-date"},
        {"name": "Fixture Naive Clock", "status": "ok", "note": "", "checked_at": "2026-07-20T01:02:03"},
    ]}
    clean = render_mod.sanitize_digest(copy.deepcopy(test_digest))
    by_name = {h["name"]: h for h in clean["source_health"]}
    bad = by_name.get("Fixture Bad Clock", {})
    if "checked_at" in bad:
        return False, f"RED FIXTURE FAILED: an unparseable checked_at survived: {bad['checked_at']!r}"
    naive = by_name.get("Fixture Naive Clock", {})
    if naive.get("checked_at") != "2026-07-20T01:02:03+00:00":
        return False, (f"GREEN FIXTURE FAILED: a naive checked_at was not normalized to "
                       f"offset-carrying ISO: {naive.get('checked_at')!r}")
    return True, None


def test_single_pass_placeholder_substitution():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import render as render_mod

    digest = _load_real_digest()
    hostile = copy.deepcopy(digest["items"][0])
    hostile["id"] = "fixture-og-placeholder"
    hostile["title"] = "Scraped headline quoting __OG_DESC__ and __OG_TITLE__ literally"
    hostile["summary"] = "Summary mentioning __DIGEST_JSON__ too"
    test_digest = {**digest,
                   "items": digest["items"] + [hostile],
                   # A backslash here rode the OG substitution into the JSON
                   # payload un-escaped under the old sequential .replace().
                   "top_of_mind": "Deadline C:\\watchlist review is top of mind."}

    with tempfile.TemporaryDirectory() as tmp:
        out_path, feed_path = Path(tmp) / "index.html", Path(tmp) / "feed.xml"
        saved = render_mod.OUTPUT_PATH, render_mod.FEED_PATH
        render_mod.OUTPUT_PATH, render_mod.FEED_PATH = out_path, feed_path
        try:
            html = render_mod.render(copy.deepcopy(test_digest))
        finally:
            render_mod.OUTPUT_PATH, render_mod.FEED_PATH = saved

    m = re.search(r"^const DIGEST = (.*);$", html, re.MULTILINE)
    if not m:
        return False, "FIXTURE BROKE: could not locate the embedded DIGEST payload in the output"
    try:
        embedded = json.loads(m.group(1))
    except ValueError as exc:
        return False, (f"RED FIXTURE FAILED: the embedded DIGEST payload is not valid JSON after "
                       f"placeholder substitution ({exc}) -- OG substitution corrupted it")
    by_id = {it["id"]: it for it in embedded["items"]}
    got = by_id.get("fixture-og-placeholder", {})
    if got.get("title") != hostile["title"] or got.get("summary") != hostile["summary"]:
        return False, (f"RED FIXTURE FAILED: literal placeholder text inside item content was "
                       f"rewritten by a later substitution pass: {got.get('title')!r} / "
                       f"{got.get('summary')!r}")
    if embedded.get("top_of_mind") != test_digest["top_of_mind"]:
        return False, (f"RED FIXTURE FAILED: top_of_mind did not round-trip: "
                       f"{embedded.get('top_of_mind')!r}")
    # And the real OG meta tags still got their real values.
    if 'content="__OG_TITLE__"' in html or 'content="__OG_DESC__"' in html:
        return False, "GREEN FIXTURE FAILED: the actual OG meta placeholders were left unsubstituted"
    return True, None


def main():
    failures = []
    for test in (test_radar_calendar_validation, test_checked_at_normalized_or_stripped,
                 test_single_pass_placeholder_substitution):
        ok, msg = test()
        print(f"{test.__name__}: {'ok' if ok else 'FAILED'}")
        if not ok:
            failures.append(msg)
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
