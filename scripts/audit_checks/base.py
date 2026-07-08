"""Shared helpers for audit check modules.

Every check module exposes:
  CHECK_ID: str            -- stable identifier; renaming a check must not
                               orphan its history in the ledger/lessons, so
                               this id (not the filename) is the identity.
  MODE: "hard" | "soft"     -- hard failures halt data/pipeline remediation
                               this run (see audit.py); soft is report-only.
  run(repo_root) -> list[Finding]

A check that cannot run (missing history, unreadable file, an import that
raised) must return a COULD_NOT_RUN finding, never an empty list -- a guard
that cannot run must be as loud as a guard that fails.
"""
import subprocess
from datetime import datetime

# History-grounded checks (deletion_diff, first_seen_3way) only reason about
# commits AT OR AFTER this moment. Every commit before it is this project's
# initial same-day build session -- rapid dev/test iteration against
# constantly-changing live feeds, not real production operation (the first
# genuine daily-cron run happens the next scheduled weekday). A monitoring
# system installed today should start watching from today, not retroactively
# flag a flurry of pre-installation test commits as incidents. Documented in
# audit/lessons.md; do not move this earlier to "cover more history" -- it
# exists specifically to exclude history that predates real operation, not
# to bound performance.
BOOTSTRAP_CUTOFF = "2026-07-07T14:00:00+00:00"


def finding(check_id, severity, title, detail, evidence=None):
    """severity: 'critical' | 'warn' | 'info' | 'could_not_run'."""
    return {
        "check": check_id,
        "severity": severity,
        "title": title,
        "detail": detail,
        "evidence": evidence or {},
    }


def could_not_run(check_id, reason, bootstrap_expected=False):
    """bootstrap_expected=True marks a could_not_run that is caused SOLELY by
    not having enough post-BOOTSTRAP_CUTOFF history yet -- a known, temporary,
    self-resolving state (see BOOTSTRAP_CUTOFF above), not a genuine fault.
    scripts/audit.py's weekly exit code still treats this as hard_failure
    (a human running the playbook should see it and understand why), but the
    daily unattended tripwire (integrity.yml) uses this flag to avoid paging
    someone every day for weeks about a condition that isn't a bug and that
    only time can fix. Never set this for a REAL could_not_run (an import
    failure, unavailable git history, etc.) -- only for the specific
    insufficient-history-since-cutoff case."""
    return finding(check_id, "could_not_run", f"{check_id} could not run", reason,
                   evidence={"bootstrap_expected": True} if bootstrap_expected else None)


def git(args, cwd):
    """Run git read-only; raise on failure (callers decide could_not_run)."""
    return subprocess.run(
        ["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True, timeout=30,
    ).stdout


def commits_touching(repo_root, path, since_days=30, after=None, keep_one_baseline_before=False):
    """(sha, author_date_iso) for every commit touching `path`, OLDEST first.

    `after` (ISO string), when given, drops commits authored before it -- see
    BOOTSTRAP_CUTOFF. When `keep_one_baseline_before` is also set, the single
    most recent commit BEFORE the cutoff is kept too, so the first real
    post-cutoff commit still has something to be diffed against instead of
    starting with no baseline at all.
    """
    out = git(["log", f"--since={since_days} days ago", "--format=%H|%aI", "--", path], repo_root)
    after_dt = datetime.fromisoformat(after) if after else None
    all_commits = []
    for line in out.strip().splitlines():
        if not line:
            continue
        sha, date = line.split("|", 1)
        all_commits.append((sha, date))
    # all_commits is newest-first (git log default order).
    if not after_dt:
        return list(reversed(all_commits))

    kept = [c for c in all_commits if datetime.fromisoformat(c[1]) >= after_dt]
    if keep_one_baseline_before:
        before = [c for c in all_commits if datetime.fromisoformat(c[1]) < after_dt]
        if before:
            kept.append(before[0])  # newest-first list -> before[0] is the closest-preceding commit
    return list(reversed(kept))


def file_at_commit(repo_root, sha, path):
    """Parsed JSON of `path` as it existed at `sha`, or None if absent/invalid."""
    import json
    try:
        raw = git(["show", f"{sha}:{path}"], repo_root)
    except subprocess.CalledProcessError:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def earliest_first_seen_by_id(repo_root, since_days=90, after=None):
    """{item id: first_seen at that id's EARLIEST git appearance}, scanning
    data/digest.json history oldest-first.

    Anchoring on `id` rather than the dedupe key (canonical URL for ordinary
    items -- see run._dedupe_key) matters because `id` is assigned exactly
    once, via `item.setdefault("id", ...)` in run.py, and is never
    reassigned afterward -- unlike the dedupe key, which is recomputed from
    the item's CURRENT `url` every time it's read. An item whose `url` is
    edited gets a brand-new dedupe key with no history under the old one,
    silently orphaning it from any check that anchors on that key alone (see
    audit/lessons.md L2). `id` survives a `url` edit unchanged, so anchoring
    on it closes that gap for both first_seen_3way and deletion_diff.

    since_days defaults wide (90, vs the ~10-12 day windows the two PROTECTED
    checks otherwise use for their own commit-pair walks) because an id's
    TRUE earliest appearance -- the whole point of an anchor -- may predate
    the narrower window either check uses to look for a recent deletion.
    """
    import json
    try:
        commits = commits_touching(repo_root, "data/digest.json", since_days=since_days, after=after)
    except Exception:
        return {}
    anchor = {}
    for sha, _date in commits:
        snap = file_at_commit(repo_root, sha, "data/digest.json")
        if not snap:
            continue
        for it in snap.get("items", []):
            item_id = it.get("id")
            if item_id and item_id not in anchor:
                anchor[item_id] = it.get("first_seen")
    return anchor
