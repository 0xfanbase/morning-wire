"""PROTECTED CORE. Reconstructs deletions from git directly -- never from a
bot-written "last known good" file, which the corrupting run itself could
poison. Walks every consecutive pair of commits touching data/digest.json in
the retention window and asserts every disappearance was legal.

A deletion is legal ONLY when the item's first_seen (as recorded in the
OLDER commit -- never the possibly-falsified current value) is already
outside DIGEST_ITEMS_MAX_AGE_DAYS relative to the newer commit's
generated_at. This is exactly run.merge_digest_window's own rule, checked
independently against history so a future confused audit run, a human
mistake, or a data-repair script cannot silently evade it.

An illegal deletion found in HISTORY (the item vanished between two old
commits) but where the item is PRESENT AGAIN in the current digest.json is
reported as 'warn', not 'critical': it already self-corrected (a human or a
prior audit run restored it) and the git record is kept for the ledger, not
as an open action item. Only a deletion whose item is STILL missing right
now is 'critical' -- something an auditor should act on today.
"""
import json
from datetime import datetime, timedelta, timezone

from base import commits_touching, file_at_commit, finding, could_not_run, BOOTSTRAP_CUTOFF

CHECK_ID = "deletion_diff"
MODE = "hard"

RETENTION_SLACK_DAYS = 10  # the pipeline's own window (8) plus slack for clock skew


def _parse_iso(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run(repo_root, bootstrap_cutoff=BOOTSTRAP_CUTOFF):
    """bootstrap_cutoff is overridable so tests can validate detection logic
    against a historical worktree on its own terms; production callers (the
    harness) always use the default -- see base.BOOTSTRAP_CUTOFF."""
    try:
        import sys
        sys.path.insert(0, str(repo_root / "scripts"))
        import run as run_mod
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import scripts/run.py: {exc}")]

    try:
        commits = commits_touching(repo_root, "data/digest.json", since_days=RETENTION_SLACK_DAYS + 2,
                                    after=bootstrap_cutoff, keep_one_baseline_before=True)
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"git history unavailable: {exc}")]
    if len(commits) < 2:
        return [could_not_run(CHECK_ID, "fewer than 2 commits to data/digest.json since BOOTSTRAP_CUTOFF -- cannot diff yet")]

    try:
        current_keys = set()
        current = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
        for it in current.get("items", []):
            try:
                current_keys.add(run_mod._dedupe_key(it))
            except Exception:
                continue
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read current data/digest.json: {exc}")]

    findings = []
    for (old_sha, _od), (new_sha, _nd) in zip(commits, commits[1:]):
        old = file_at_commit(repo_root, old_sha, "data/digest.json")
        new = file_at_commit(repo_root, new_sha, "data/digest.json")
        if not old or not new:
            continue
        new_gen = _parse_iso(new.get("generated_at"))
        if not new_gen:
            continue

        old_by_key = {}
        for it in old.get("items", []):
            try:
                key = run_mod._dedupe_key(it)
            except Exception:
                continue
            if key:
                old_by_key[key] = it

        new_keys = set()
        for it in new.get("items", []):
            try:
                key = run_mod._dedupe_key(it)
            except Exception:
                continue
            if key:
                new_keys.add(key)

        cutoff = new_gen - timedelta(days=RETENTION_SLACK_DAYS)
        for key, old_it in old_by_key.items():
            if key in new_keys:
                continue
            old_fs = _parse_iso(old_it.get("first_seen"))
            if old_fs is None:
                # Can't prove legality either way -- report, don't assume innocent.
                findings.append(finding(
                    CHECK_ID, "warn",
                    f"item disappeared with unparseable first_seen: '{old_it.get('title', '')[:60]}'",
                    f"Present in {old_sha[:8]}, absent in {new_sha[:8]}; first_seen={old_it.get('first_seen')!r} "
                    "could not be parsed, so legality of the deletion could not be verified.",
                    {"key": key, "old_sha": old_sha, "new_sha": new_sha},
                ))
                continue
            if old_fs >= cutoff:
                still_missing = key not in current_keys
                findings.append(finding(
                    CHECK_ID, "critical" if still_missing else "warn",
                    (f"item deleted while still inside the retention window: '{old_it.get('title', '')[:60]}'"
                     if still_missing else
                     f"item was illegally deleted then restored: '{old_it.get('title', '')[:60]}'"),
                    f"Present in {old_sha[:8]} with first_seen={old_it.get('first_seen')}, absent in "
                    f"{new_sha[:8]} (generated_at={new.get('generated_at')}). That first_seen is only "
                    f"{(new_gen - old_fs).days} day(s) before generated_at -- inside the retention "
                    "window, so this is an illegal deletion, not a legitimate age-out."
                    + ("" if still_missing else " The item is present again in the current digest.json -- "
                       "this is a historical record for the ledger, not an open action item."),
                    {"key": key, "id": old_it.get("id"), "old_sha": old_sha, "new_sha": new_sha,
                     "first_seen": old_it.get("first_seen"), "new_generated_at": new.get("generated_at"),
                     "still_missing": still_missing},
                ))

    return findings
