"""Red/green fixture for check_deletion_diff's age-out legality boundary.

check_deletion_diff originally judged a deletion's legality against
RETENTION_SLACK_DAYS (10 days) instead of the pipeline's own retention rule
(run.DIGEST_ITEMS_MAX_AGE_DAYS = 8, the exact rule its docstring claims to
enforce). The pipeline prunes an item the first run after its first_seen
passes 8 days -- at which point the item is 8-9 days old, i.e. still inside
a 10-day "window" -- so every single routine age-out would have been
reported as a critical "illegal deletion" (and, via the daily tripwire,
opened a non-suppressible integrity issue: deletion_diff is PROTECTED, its
criticals can never be excepted). Found by inspection on 2026-07-13, two
days before the first natural age-outs in this repo's live data would have
fired ~44 false criticals at once.

Cases:
  red   -- a legal age-out (first_seen 8.5 days before the newer commit's
           generated_at, exactly what merge_digest_window prunes) must NOT
           be flagged. Fails on the pre-fix code, passes on the fixed code.
  green -- a deletion 2 days after first_seen (the L1 incident shape) must
           still be flagged critical: the fix must not weaken real
           detection.
  green -- a deletion just INSIDE the window (7 days 22 hours old: more
           than LEGALITY_SKEW_TOLERANCE short of the 8-day rule) must still
           be flagged critical.
  green -- an age-out measured a few minutes short of 8 days against
           generated_at (run.py stamps generated_at before the slow
           verify/summarise steps, merge_digest_window prunes on a later
           "now") must be excused by LEGALITY_SKEW_TOLERANCE.

Run manually: python3 scripts/audit_checks/fixtures/test_deletion_diff_ageout_boundary.py
Exits non-zero if any assertion fails.
"""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True, timeout=30).stdout


def _init_repo(tmp):
    repo = Path(tmp)
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Fixture"], repo)
    (repo / "data").mkdir()
    (repo / "data" / "seen-items.json").write_text("{}", encoding="utf-8")
    return repo


def _write_digest(repo, generated_at, items):
    (repo / "data" / "digest.json").write_text(
        json.dumps({"generated_at": generated_at, "items": items, "source_health": [], "run_log": []}, indent=2),
        encoding="utf-8",
    )


def _commit(repo, message):
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", message], repo)


def _base_item(item_id, url, first_seen):
    return {
        "id": item_id, "jurisdiction": "GLOBAL", "source": "Test Source",
        "title": f"Test item {item_id}", "url": url, "published": first_seen, "type": "news",
        "priority": "normal", "status": "new",
        "verification": {"level": "single_source", "sources": [{"name": "Test Source", "url": url}]},
        "summary": "Test", "so_what": "Test", "first_seen": first_seen,
    }


def _critical_ids(findings):
    return {f.get("evidence", {}).get("id") for f in findings if f["severity"] == "critical"}


def test_ageout_boundary():
    import importlib
    import check_deletion_diff as cdd
    importlib.reload(cdd)

    now = datetime.now(timezone.utc)
    new_gen = now.isoformat()

    # first_seen values, all relative to the NEWER commit's generated_at:
    legal_ageout_fs = (now - timedelta(days=8, hours=12)).isoformat()   # pipeline's own prune
    skew_ageout_fs = (now - timedelta(days=8) + timedelta(minutes=30)).isoformat()  # legal prune, generated_at skew
    inside_window_fs = (now - timedelta(days=7, hours=22)).isoformat()  # 2h short of the rule: illegal
    young_fs = (now - timedelta(days=2)).isoformat()                    # the L1 shape: clearly illegal

    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(tmp)

        items = [
            _base_item("legal-ageout-001", "https://example.com/legal", legal_ageout_fs),
            _base_item("skew-ageout-002", "https://example.com/skew", skew_ageout_fs),
            _base_item("inside-window-003", "https://example.com/inside", inside_window_fs),
            _base_item("young-004", "https://example.com/young", young_fs),
            _base_item("keeper-005", "https://example.com/keeper", young_fs),
        ]
        _write_digest(repo, (now - timedelta(days=1)).isoformat(), items)
        _commit(repo, "day N: all five items present")

        # Day N+1: only the keeper survives. The two age-outs are what
        # merge_digest_window itself would do (plus same-run skew); the
        # other two deletions are illegal.
        _write_digest(repo, new_gen, [_base_item("keeper-005", "https://example.com/keeper", young_fs)])
        _commit(repo, "day N+1: age-outs plus two illegal deletions")

        flagged = _critical_ids(cdd.run(repo, bootstrap_cutoff=None))

        assert "legal-ageout-001" not in flagged, (
            "RED CASE REGRESSED: a legal 8.5-day age-out (the pipeline's own retention prune) "
            f"was flagged as an illegal deletion. Flagged ids: {flagged}"
        )
        print("test_ageout_boundary: legal 8.5-day age-out correctly NOT flagged")

        assert "skew-ageout-002" not in flagged, (
            "a legal age-out measured 30 minutes short of the window against generated_at "
            f"(same-run skew) was flagged -- LEGALITY_SKEW_TOLERANCE is not working. Flagged ids: {flagged}"
        )
        print("test_ageout_boundary: same-run-skew age-out correctly excused")

        assert "inside-window-003" in flagged, (
            "GREEN CASE REGRESSED: a deletion 2 hours inside the retention window was NOT flagged -- "
            f"the boundary fix over-weakened the check. Flagged ids: {flagged}"
        )
        print("test_ageout_boundary: deletion 2h inside the window still flagged critical")

        assert "young-004" in flagged, (
            "GREEN CASE REGRESSED: a 2-day-old deletion (the L1 incident shape) was NOT flagged. "
            f"Flagged ids: {flagged}"
        )
        print("test_ageout_boundary: 2-day-old deletion (L1 shape) still flagged critical")


def main():
    try:
        test_ageout_boundary()
    except AssertionError as exc:
        print(f"test_ageout_boundary: FAIL -- {exc}")
        sys.exit(1)
    print("\nAll deletion_diff age-out boundary assertions pass.")
    sys.exit(0)


if __name__ == "__main__":
    main()
