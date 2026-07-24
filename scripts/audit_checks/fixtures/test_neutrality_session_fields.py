"""Red/green fixture: check_neutrality_secrets must scan the session-authored
public text OUTSIDE items -- top_of_mind, run_log[].note, radar[].label,
source_health[].note -- with the same voice patterns it already applies to
item title/summary/so_what. All four render on the public page and are
written by the same enrichment/audit sessions the item scan exists to catch,
so items-only scanning left them a blind spot.

Builds a synthetic repo in a scratch directory. The secret-pattern half of
the check needs `git ls-files`, so the scratch repo is a real (empty-ish)
git repo rather than a bare directory.

Run manually: python3 scripts/audit_checks/fixtures/test_neutrality_session_fields.py
Exits non-zero if any assertion fails.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _repo(tmp, digest):
    root = Path(tmp)
    (root / "data").mkdir()
    (root / "data" / "digest.json").write_text(json.dumps(digest), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, capture_output=True)
    return root


def _digest(**overrides):
    base = {
        "generated_at": "2026-07-20T21:00:00+00:00",
        "top_of_mind": "",
        "items": [],
        "run_log": [{"at": "2026-07-20T21:00:00+00:00", "note": "Daily fetch: 3 new items surfaced"}],
        "radar": [{"date": "2026-08-01", "label": "Comments close: example consultation", "jurisdiction": "US"}],
        "source_health": [{"name": "Example Wire", "status": "ok", "note": "24 items"}],
    }
    base.update(overrides)
    return base


def main():
    failures = []
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    import check_neutrality_secrets as check

    red_cases = {
        "top_of_mind": _digest(top_of_mind="Our bank should tighten screening today."),
        "run_log[].note": _digest(run_log=[{"at": "2026-07-20T21:00:00+00:00",
                                            "note": "Enrichment: we recommend reviewing the new rule."}]),
        "radar[].label": _digest(radar=[{"date": "2026-08-01", "jurisdiction": "US",
                                          "label": "Deadline for your firm's comment letter"}]),
        "source_health[].note": _digest(source_health=[{"name": "Example Wire", "status": "ok",
                                                         "note": "we advise switching feeds"}]),
    }
    for field, digest in red_cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            findings = check.run(_repo(tmp, digest))
            voice = [f for f in findings if "first-person voice" in f["title"]]
            print(f"RED case ({field}): {len(voice)} voice finding(s)")
            if not any(field.split("[")[0] in f["evidence"].get("field", "") for f in voice):
                failures.append(f"RED FIXTURE FAILED: institutional voice in {field} was not flagged")

    with tempfile.TemporaryDirectory() as tmp:
        findings = check.run(_repo(tmp, _digest(
            top_of_mind="A regulator finalised a rule; screening lists change next month.")))
        voice = [f for f in findings if "first-person voice" in f["title"]]
        print(f"GREEN case (neutral prose everywhere): {len(voice)} voice finding(s)")
        if voice:
            failures.append(f"GREEN FIXTURE FAILED: neutral session-authored prose was flagged -- {voice}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
