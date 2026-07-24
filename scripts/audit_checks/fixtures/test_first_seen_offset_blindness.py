"""Red/green fixture: check_first_seen_3way must compare first_seen values
as INSTANTS, not raw ISO strings.

render._normalize_iso preserves whatever UTC offset it parses, and the
enrichment recipe (CLAUDE.md step 7) legitimately writes date-only values as
midnight +08:00 -- so digest history genuinely mixes offsets. A
lexicographic compare only agrees with the timeline when both sides share an
offset:

  RED  (anchor gate): current "...T08:00:00+08:00" (= 00:00Z) string-sorts
       AFTER anchor "...T02:00:00+00:00" (= 02:00Z) despite being two hours
       EARLIER -- a genuine backdate the string gate waves through.
  GREEN (anchor gate): the same instant re-written at a different offset
       ("T10:00+08:00" -> "T02:00+00:00") is NOT a backdate and must not
       fire -- the string compare false-positived on exactly this.
  RED  (seen-items gate): the old `[:16]` wall-clock prefix compare calls
       "T08:00+08:00" and "T08:00+00:00" equal -- two instants EIGHT HOURS
       apart -- and stays silent.
  GREEN (seen-items gate): sub-minute clock noise across offsets
       ("T08:00:00+08:00" vs "T00:00:20+00:00", 20s apart) must stay quiet,
       preserving the old compare's same-minute tolerance.

Builds synthetic git repos in scratch directories (same approach as
test_l4_workflow_injection.py -- this is a proactively-found gap, not a
replay of a historical incident commit).

Run manually: python3 scripts/audit_checks/fixtures/test_first_seen_offset_blindness.py
Exits non-zero if any assertion fails.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _item(first_seen, url="https://example.org/story-a", item_id="us-example-0000000001"):
    return {
        "id": item_id, "jurisdiction": "US", "source": "Example Wire", "title": "Example story",
        "url": url, "published": "2026-07-20T00:00:00+00:00", "type": "news",
        "priority": "normal", "status": "new",
        "verification": {"level": "single_source", "sources": [{"name": "Example Wire", "url": url}]},
        "summary": "Example story", "so_what": "Review the source directly.",
        "first_seen": first_seen,
    }


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid"] + list(args),
        cwd=str(repo), check=True, capture_output=True, text=True,
    )


def _repo_with_history(tmp, first_seen_versions, seen_first_seen_by_url=None):
    """A git repo whose data/digest.json went through the given first_seen
    values (one commit per version, oldest first). Each version is either a
    single first_seen string (one item) or a list of (url, first_seen)."""
    repo = Path(tmp)
    (repo / "data").mkdir()
    _git(repo, "init", "-q")
    for version in first_seen_versions:
        if isinstance(version, str):
            items = [_item(version)]
        else:
            items = [_item(fs, url=url, item_id=f"us-example-{i:010d}") for i, (url, fs) in enumerate(version)]
        (repo / "data" / "digest.json").write_text(json.dumps({
            "generated_at": "2026-07-20T21:00:00+00:00", "items": items,
        }), encoding="utf-8")
        _git(repo, "add", "data/digest.json")
        _git(repo, "commit", "-q", "-m", "digest update")
    seen = {}
    for url, fs in (seen_first_seen_by_url or {}).items():
        seen[url] = {"title_hash": "x", "first_seen": fs, "last_seen": fs, "title": "Example story"}
    (repo / "data" / "seen-items.json").write_text(json.dumps(seen), encoding="utf-8")
    return repo


def main():
    failures = []
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import run  # noqa: F401 -- pre-import so the check resolves the REAL pipeline module
    import check_first_seen_3way as check

    url = "https://example.org/story-a"

    # RED: 2h backdate hidden behind a differing offset.
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_history(
            tmp,
            ["2026-07-20T02:00:00+00:00",   # anchor: first seen 02:00Z
             "2026-07-20T08:00:00+08:00"],  # current: 00:00Z -- 2h EARLIER, string-sorts later
            seen_first_seen_by_url={url: "2026-07-20T08:00:00+08:00"},  # agrees with current
        )
        criticals = [f for f in check.run(repo) if f["severity"] == "critical"]
        print(f"RED case (offset-hidden 2h backdate): {len(criticals)} critical finding(s)")
        if not any("backdated" in f["title"] for f in criticals):
            failures.append("RED FIXTURE FAILED: a genuine 2h backdate hidden behind a +08:00 offset "
                             "was not flagged against the git anchor")

    # GREEN: same instant, offset representation changed -- must NOT fire.
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_history(
            tmp,
            ["2026-07-20T10:00:00+08:00",   # anchor: 02:00Z
             "2026-07-20T02:00:00+00:00"],  # current: the SAME instant, re-normalized
            seen_first_seen_by_url={url: "2026-07-20T02:00:00+00:00"},
        )
        criticals = [f for f in check.run(repo) if f["severity"] == "critical"]
        print(f"GREEN case (same instant, offset re-written): {len(criticals)} critical finding(s)")
        if criticals:
            failures.append(f"GREEN FIXTURE FAILED: an offset re-normalization of the SAME instant "
                             f"was flagged as a backdate -- {criticals}")

    # RED + GREEN for the seen-items.json gate, one item each.
    url_b = "https://example.org/story-b"
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with_history(
            tmp,
            [[(url, "2026-07-20T08:00:00+08:00"),      # item A
              (url_b, "2026-07-20T08:00:00+08:00")]],  # item B (single commit: anchors agree)
            seen_first_seen_by_url={
                url: "2026-07-20T08:00:00+00:00",    # A: same [:16] prefix, EIGHT HOURS apart -> must fire
                url_b: "2026-07-20T00:00:20+00:00",  # B: 20s clock noise across offsets -> must stay quiet
            },
        )
        criticals = [f for f in check.run(repo) if f["severity"] == "critical"]
        disagree = [f for f in criticals if "disagrees" in f["title"]]
        print(f"seen-items gate: {len(disagree)} disagreement finding(s)")
        if not any(f["evidence"].get("seen_items_first_seen") == "2026-07-20T08:00:00+00:00" for f in disagree):
            failures.append("RED FIXTURE FAILED: an 8h digest-vs-seen-items disagreement with matching "
                             "[:16] prefixes was not flagged")
        if any(f["evidence"].get("seen_items_first_seen") == "2026-07-20T00:00:20+00:00" for f in disagree):
            failures.append("GREEN FIXTURE FAILED: 20s cross-offset clock noise was flagged as a "
                             "disagreement (the old same-minute tolerance was lost)")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
