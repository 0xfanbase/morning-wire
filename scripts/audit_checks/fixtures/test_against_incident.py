"""Regression test for the protected checks: proves check_first_seen_3way
and check_deletion_diff actually FIRE against the real corrupted commit from
the 2026-07-07 incident (cd206e3), and are CLEAN against the current,
repaired HEAD. This is the "red fixture / green fixture" the design calls
for, built from the real incident rather than a synthetic approximation.

Run manually: python3 scripts/audit_checks/fixtures/test_against_incident.py
Exits non-zero if either assertion fails.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
INCIDENT_SHA = "cd206e3"  # "Fact-checking layer, date provenance, ... + 7-day backfill" -- the corrupting commit


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True, timeout=60).stdout


def main():
    failures = []

    with tempfile.TemporaryDirectory() as tmp:
        worktree = Path(tmp) / "incident-worktree"
        _git(["worktree", "add", "--detach", str(worktree), INCIDENT_SHA], REPO_ROOT)
        try:
            sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
            import check_first_seen_3way as c1
            import check_deletion_diff as c2

            # bootstrap_cutoff=None: the red fixture validates detection logic
            # against the INCIDENT'S OWN historical context, not today's live
            # production cutoff (which postdates the incident commit itself
            # and would otherwise exclude it entirely -- see base.BOOTSTRAP_CUTOFF).
            findings_3way = c1.run(worktree, bootstrap_cutoff=None)
            criticals_3way = [f for f in findings_3way if f["severity"] == "critical"]
            print(f"check_first_seen_3way @ {INCIDENT_SHA}: {len(criticals_3way)} critical finding(s)")
            if not criticals_3way:
                failures.append(f"RED FIXTURE FAILED: check_first_seen_3way found nothing at the known-bad commit {INCIDENT_SHA}")

            findings_del = c2.run(worktree, bootstrap_cutoff=None)
            criticals_del = [f for f in findings_del if f["severity"] == "critical"]
            print(f"check_deletion_diff @ {INCIDENT_SHA}: {len(criticals_del)} critical finding(s)")
            if not criticals_del:
                failures.append(f"RED FIXTURE FAILED: check_deletion_diff found nothing at the known-bad commit {INCIDENT_SHA}")
        finally:
            _git(["worktree", "remove", "--force", str(worktree)], REPO_ROOT)

    # Green fixture: current HEAD must be clean of the SPECIFIC incident
    # (the 4 recovered items must not still show as backdated/deleted).
    # We do NOT assert zero findings at HEAD: this repo's entire git history
    # so far is one rapid same-day build session, full of legitimate dev
    # iterations (test runs against live feeds that fetched completely
    # different content run to run) that look shape-identical to a real
    # deletion. That noise ages out of the window on its own as real daily
    # commits accumulate -- asserting "zero ever" would be a false promise
    # on a brand-new repo, not a meaningful safety property.
    # Exact dedupe keys of the 4 items actually recovered by this session's
    # fix (PR #19) -- precise keys, not a fuzzy title match, so an UNRELATED
    # older artifact sharing similar wording (there is one: an early-dev-
    # iteration hkma.gov.hk URL variant of the FSTB story, from long before
    # the incident, superseded by the info.gov.hk URL and never itself part
    # of the incident) can't produce a false regression signal.
    INCIDENT_KEYS = {
        "https://www.info.gov.hk/gia/general/202606/29/P2026062900677.htm",
        "https://www.eba.europa.eu/publications-and-media/press-releases/european-banking-authority-consults-draft-methodology-setting-fines-under-markets-crypto-assets",
        "https://www.eba.europa.eu/publications-and-media/press-releases/eba-updates-validation-rules-supervisory-reporting",
    }

    # Production mode (default bootstrap_cutoff): today, this correctly
    # returns COULD_NOT_RUN -- there isn't yet 1-2 real post-install commits
    # for these checks to reason about (the whole repo is pre-cutoff dev
    # history). That's expected, not a failure; assert only that neither
    # check crashes and that IF it found anything, none of it is the
    # resolved incident.
    findings_3way_head = c1.run(REPO_ROOT)
    incident_hits = [f for f in findings_3way_head if f["severity"] == "critical"
                     and f.get("evidence", {}).get("key") in INCIDENT_KEYS]
    print(f"check_first_seen_3way @ HEAD (production mode): {len(findings_3way_head)} total finding(s), "
          f"{len(incident_hits)} matching the resolved incident")
    if incident_hits:
        failures.append(f"REGRESSION: the resolved incident items are flagged again at HEAD: {incident_hits}")

    findings_del_head = c2.run(REPO_ROOT)
    incident_hits_del = [f for f in findings_del_head if f["severity"] == "critical"
                          and f.get("evidence", {}).get("key") in INCIDENT_KEYS]
    print(f"check_deletion_diff @ HEAD (production mode): {len(findings_del_head)} total finding(s), "
          f"{len(incident_hits_del)} matching the resolved incident")
    if incident_hits_del:
        failures.append(f"REGRESSION: the resolved incident items are flagged again at HEAD: {incident_hits_del}")

    # Also validate WITHOUT the bootstrap cutoff (full history, same mode the
    # red fixture used) so the green fixture still meaningfully exercises the
    # detection logic today, not just "there was no history to look at yet".
    findings_3way_full = c1.run(REPO_ROOT, bootstrap_cutoff=None)
    incident_hits_full = [f for f in findings_3way_full if f["severity"] == "critical"
                          and f.get("evidence", {}).get("key") in INCIDENT_KEYS]
    print(f"check_first_seen_3way @ HEAD (full history): {len(findings_3way_full)} total finding(s), "
          f"{len(incident_hits_full)} matching the resolved incident")
    if incident_hits_full:
        failures.append(f"REGRESSION (full history): the resolved incident items are flagged again: {incident_hits_full}")

    findings_del_full = c2.run(REPO_ROOT, bootstrap_cutoff=None)
    incident_hits_del_full = [f for f in findings_del_full if f["severity"] == "critical"
                              and f.get("evidence", {}).get("key") in INCIDENT_KEYS]
    print(f"check_deletion_diff @ HEAD (full history): {len(findings_del_full)} total finding(s) "
          f"(expected: nonzero, from this repo's same-day dev-iteration history -- see docstring), "
          f"{len(incident_hits_del_full)} matching the resolved incident")
    if incident_hits_del_full:
        failures.append(f"REGRESSION (full history): the resolved incident items are flagged again: {incident_hits_del_full}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nAll incident regression assertions pass.")
    sys.exit(0)


if __name__ == "__main__":
    main()
