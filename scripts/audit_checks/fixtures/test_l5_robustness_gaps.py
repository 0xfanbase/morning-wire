"""Red/green fixtures for audit/lessons.md L5: a set of smaller robustness
gaps found by the same 4-new-angle Fable audit round that found L4's
workflow-injection issue (2026-07-08). Each covers one closed gap:

1. check_render_drops.py: duplicate-id masking. Two items sharing one id,
   where sanitize_digest keeps one and drops the other, must still be
   reported -- a plain before/after id-SET comparison sees the id present on
   both sides and reports nothing.
2. render.sanitize_digest: control characters (illegal in XML 1.0) and lone
   surrogate code points (which crash UTF-8 encoding outright) in a title/
   summary/so_what must be stripped, not passed through to feed.xml or the
   JSON embed.
3. check_docs_feed_parity.py: an unparseable docs/feed.xml must be CRITICAL,
   not warn -- it is total breakage for every RSS reader, not a soft signal.
4. check_enum_constant_freeze.py: a jurisdiction code's MEANING (its
   page.html JURIS_FULL display name, its JURIS_ORDER position), not just
   the set of valid codes, must be frozen -- silently relabeling "HK" to
   "United States" in JURIS_FULL must trip the freeze.

Run manually: python3 scripts/audit_checks/fixtures/test_l5_robustness_gaps.py
Exits non-zero if any assertion fails.
"""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _load_real_digest():
    return json.loads((REPO_ROOT / "data" / "digest.json").read_text(encoding="utf-8"))


def test_render_drops_duplicate_id():
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_render_drops as check

    digest = _load_real_digest()
    good = copy.deepcopy(digest["items"][0])
    good["id"] = "fixture-dup-id"
    bad = copy.deepcopy(digest["items"][0])
    bad["id"] = "fixture-dup-id"
    del bad["type"]  # fails _valid_item's required-field check

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "data").mkdir()
        (root / "scripts").symlink_to(REPO_ROOT / "scripts")
        test_digest = {**digest, "items": [good, bad]}
        (root / "data" / "digest.json").write_text(json.dumps(test_digest), encoding="utf-8")
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        if not criticals or "fixture-dup-id" not in criticals[0]["evidence"]["dropped_ids"]:
            return False, f"RED FIXTURE FAILED: duplicate-id drop not detected: {findings}"
    return True, None


def test_sanitize_strips_illegal_chars():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import render as render_mod

    digest = _load_real_digest()
    poisoned = copy.deepcopy(digest["items"][0])
    poisoned["id"] = "fixture-control-chars"
    poisoned["title"] = "Fed \x1bhikes\x00 rates"
    poisoned["summary"] = "Summary with \x0b\x0c control chars"
    poisoned["so_what"] = "so what \x7f del char"
    lone_surrogate = copy.deepcopy(digest["items"][0])
    lone_surrogate["id"] = "fixture-lone-surrogate"
    lone_surrogate["title"] = "Lone surrogate \ud83d test"

    test_digest = {**digest, "items": digest["items"] + [poisoned, lone_surrogate]}
    clean = render_mod.sanitize_digest(test_digest)
    by_id = {it["id"]: it for it in clean["items"]}

    if "\x1b" in by_id["fixture-control-chars"]["title"] or "\x00" in by_id["fixture-control-chars"]["title"]:
        return False, "RED FIXTURE FAILED: control chars survived in title"
    if "\x0b" in by_id["fixture-control-chars"]["summary"]:
        return False, "RED FIXTURE FAILED: control chars survived in summary"
    try:
        by_id["fixture-lone-surrogate"]["title"].encode("utf-8")
    except UnicodeEncodeError as exc:
        return False, f"RED FIXTURE FAILED: lone surrogate still crashes UTF-8 encode: {exc}"

    # Green: the feed itself must still parse with the poisoned items in it.
    import xml.etree.ElementTree as ET
    scratch_feed = Path(tempfile.mkstemp(suffix=".xml")[1])
    orig_feed_path = render_mod.FEED_PATH
    render_mod.FEED_PATH = scratch_feed
    try:
        render_mod.render_feed(clean)
        ET.parse(str(scratch_feed))
    except ET.ParseError as exc:
        return False, f"RED FIXTURE FAILED: feed.xml still fails to parse with poisoned input: {exc}"
    finally:
        render_mod.FEED_PATH = orig_feed_path
        scratch_feed.unlink(missing_ok=True)
    return True, None


def test_docs_feed_parity_unparseable_is_critical():
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_docs_feed_parity as check
    import render as render_mod

    digest = _load_real_digest()
    clean = render_mod.sanitize_digest(digest)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "data").mkdir()
        (root / "docs").mkdir()
        (root / "scripts").symlink_to(REPO_ROOT / "scripts")
        (root / "data" / "digest.json").write_text(json.dumps(digest), encoding="utf-8")
        # Build a minimal valid index.html with the real embed, then a
        # deliberately corrupted feed.xml alongside it. Deliberately does NOT
        # call render_mod.render() -- that writes to the real, hardcoded
        # OUTPUT_PATH/FEED_PATH (the live docs/ files), not this scratch dir.
        embed = render_mod._safe_json_embed(clean)
        (root / "docs" / "index.html").write_text(
            f"<html><script>\nconst DIGEST = {embed};\n</script></html>\n", encoding="utf-8")
        (root / "docs" / "feed.xml").write_text("<rss><channel><item><title>unterminated", encoding="utf-8")

        findings = check.run(root)
        parse_findings = [f for f in findings if "could not be parsed" in f["title"]]
        if not parse_findings:
            return False, "RED FIXTURE FAILED: no finding for unparseable feed.xml"
        if parse_findings[0]["severity"] != "critical":
            return False, f"RED FIXTURE FAILED: unparseable feed.xml is {parse_findings[0]['severity']}, expected critical"
    return True, None


def test_enum_constant_freeze_catches_juris_relabeling():
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    import check_enum_constant_freeze as check

    real_page_html = (REPO_ROOT / "scripts" / "templates" / "page.html").read_text(encoding="utf-8")
    real_snapshot = (REPO_ROOT / "audit" / "enum-snapshot.json").read_text(encoding="utf-8")
    # Relabel "HK": "Hong Kong" -> "HK": "United States" -- a real accidental
    # bug shape (e.g. a bad find/replace), not a contrived string.
    poisoned_page_html = real_page_html.replace(
        'HK: "Hong Kong"', 'HK: "United States"')
    if poisoned_page_html == real_page_html:
        return False, "RED FIXTURE SETUP FAILED: expected JURIS_FULL string not found in page.html"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        scratch_scripts = root / "scripts"
        scratch_scripts.mkdir()
        for item in (REPO_ROOT / "scripts").iterdir():
            if item.name == "templates":
                continue
            (scratch_scripts / item.name).symlink_to(item)
        (scratch_scripts / "templates").mkdir()
        (scratch_scripts / "templates" / "page.html").write_text(poisoned_page_html, encoding="utf-8")
        (root / "audit").mkdir()
        (root / "audit" / "enum-snapshot.json").write_text(real_snapshot, encoding="utf-8")

        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"
                     and f["evidence"].get("key") == "PAGE_HTML_JURIS_FULL"]
        if not criticals:
            return False, f"RED FIXTURE FAILED: JURIS_FULL relabeling not detected: {findings}"

    # Green: the real, unmodified repo must be clean for this specific key.
    findings = check.run(REPO_ROOT)
    juris_findings = [f for f in findings if f["evidence"].get("key") in
                       ("PAGE_HTML_JURIS_FULL", "PAGE_HTML_JURIS_ORDER")]
    if juris_findings:
        return False, f"GREEN FIXTURE FAILED: live repo shows drift that shouldn't exist: {juris_findings}"
    return True, None


def main():
    tests = [
        test_render_drops_duplicate_id,
        test_sanitize_strips_illegal_chars,
        test_docs_feed_parity_unparseable_is_critical,
        test_enum_constant_freeze_catches_juris_relabeling,
    ]
    failures = []
    for t in tests:
        ok, msg = t()
        print(f"{t.__name__}: {'PASS' if ok else 'FAIL'}")
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
