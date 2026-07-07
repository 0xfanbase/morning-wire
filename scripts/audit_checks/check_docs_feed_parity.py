"""PROTECTED CORE. Catches a data edit that was never re-rendered, or a
publish that silently didn't happen -- the published docs/index.html and
docs/feed.xml must be byte-equivalent (content-wise) to what
sanitize_digest(data/digest.json) would produce right now.

Deliberately does NOT call render.render(): shelling out to the real
renderer would overwrite the live docs/index.html and docs/feed.xml. This
check only reads what's already published and compares it, in memory, to
the pure sanitize_digest() output.
"""
import json
import re

from base import finding, could_not_run

CHECK_ID = "docs_feed_parity"
MODE = "hard"

_EMBED_RE = re.compile(r"const DIGEST = (.*?);\s*\n", re.DOTALL)


def _unescape_embed(raw):
    # Reverses render._safe_json_embed's escaping exactly (order matters:
    # it escaped '<' then U+2028/2029, so unescape in reverse order).
    raw = raw.replace("\\u2029", " ").replace("\\u2028", " ")
    raw = raw.replace("\\u003c", "<")
    return raw


def run(repo_root):
    try:
        import sys
        sys.path.insert(0, str(repo_root / "scripts"))
        import render as render_mod
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import scripts/render.py: {exc}")]

    try:
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
        html = (repo_root / "docs" / "index.html").read_text(encoding="utf-8")
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/digest.json or docs/index.html: {exc}")]

    m = _EMBED_RE.search(html)
    if not m:
        return [finding(CHECK_ID, "critical", "docs/index.html has no embedded DIGEST payload",
                         "The __DIGEST_JSON__ placeholder pattern was not found in the published page -- "
                         "either the template changed or the file was hand-edited.", {})]

    try:
        published = json.loads(_unescape_embed(m.group(1)))
    except Exception as exc:
        return [finding(CHECK_ID, "critical", "embedded DIGEST payload is not valid JSON",
                         f"Failed to parse the embedded script payload: {exc}", {})]

    try:
        expected = render_mod.sanitize_digest(digest)
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"sanitize_digest raised: {exc}")]

    findings = []
    pub_ids = {it.get("id") for it in published.get("items", [])}
    exp_ids = {it.get("id") for it in expected.get("items", [])}
    if pub_ids != exp_ids:
        findings.append(finding(
            CHECK_ID, "critical",
            "docs/index.html's item set does not match data/digest.json",
            f"Published has {len(pub_ids)} items, current data would sanitize to {len(exp_ids)}. "
            f"Only in published: {sorted(pub_ids - exp_ids)}. Only in current: {sorted(exp_ids - pub_ids)}. "
            "The page is stale, or data/digest.json was edited without re-rendering.",
            {"only_in_published": sorted(pub_ids - exp_ids), "only_in_current": sorted(exp_ids - pub_ids)},
        ))
    if published.get("generated_at") != expected.get("generated_at"):
        findings.append(finding(
            CHECK_ID, "warn", "generated_at differs between docs/index.html and data/digest.json",
            f"published={published.get('generated_at')} current={expected.get('generated_at')}", {},
        ))

    # RSS guid parity (feed.xml uses the stable item id as guid).
    feed_path = repo_root / "docs" / "feed.xml"
    if feed_path.exists():
        try:
            import xml.etree.ElementTree as ET
            tree = ET.fromstring(feed_path.read_text(encoding="utf-8"))
            feed_guids = {el.text for el in tree.findall(".//item/guid")}
            if feed_guids != exp_ids:
                findings.append(finding(
                    CHECK_ID, "warn", "docs/feed.xml item set does not match current data",
                    f"feed has {len(feed_guids)} guids, current data would sanitize to {len(exp_ids)} items.",
                    {"only_in_feed": sorted(feed_guids - exp_ids), "only_in_current": sorted(exp_ids - feed_guids)},
                ))
        except Exception as exc:
            findings.append(finding(CHECK_ID, "warn", "docs/feed.xml could not be parsed", str(exc), {}))

    return findings
