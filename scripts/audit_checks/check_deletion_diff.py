"""PROTECTED CORE. Reconstructs deletions from git directly -- never from a
bot-written "last known good" file, which the corrupting run itself could
poison. Walks every consecutive pair of commits touching data/digest.json in
the retention window and asserts every disappearance was legal.

A deletion is legal ONLY when the item's first_seen (as recorded in the
OLDER commit -- never the possibly-falsified current value) is already
outside DIGEST_ITEMS_MAX_AGE_DAYS relative to the newer commit's
generated_at (plus LEGALITY_SKEW_TOLERANCE -- see below). This is exactly
run.merge_digest_window's own rule, checked independently against history
so a future confused audit run, a human mistake, or a data-repair script
cannot silently evade it.

An illegal deletion found in HISTORY (the item vanished between two old
commits) but where the item is PRESENT AGAIN in the current digest.json is
reported as 'warn', not 'critical': it already self-corrected (a human or a
prior audit run restored it) and the git record is kept for the ledger, not
as an open action item. Only a deletion whose item is STILL missing right
now is 'critical' -- something an auditor should act on today.

Two hardenings from audit/lessons.md L2, on top of the original design:
1. Matching old-vs-new items by `id` in addition to the dedupe key (a URL
   for ordinary items). An item whose `url` changes gets a brand-new dedupe
   key with no match in the newer commit -- keyed matching ALONE would
   misreport that as a deletion. `id` survives a `url` edit (assigned once,
   never reassigned -- see run.py), so an id match means "still present,
   just relinked", not "gone".
2. Judging legality against the item's id-anchored EARLIEST-ever-recorded
   first_seen (base.earliest_first_seen_by_id), not just the immediately
   preceding commit's own first_seen value. The original design already
   read "the OLDER commit, never the current value" specifically to avoid
   trusting a possibly-falsified *current* first_seen -- but the older
   commit's own value can ALSO already be falsified by an earlier, separate
   edit (backdate now, delete later -- two small commits, neither alone
   looking exactly like the single-commit L1 pattern). The full-history
   anchor is immune to that: it's whatever was recorded the very first time
   this id ever appeared, before any repair/corruption could have touched it.
"""
import json
from datetime import datetime, timedelta, timezone

from base import commits_touching, earliest_first_seen_by_id, file_at_commit, finding, could_not_run, BOOTSTRAP_CUTOFF

CHECK_ID = "deletion_diff"
MODE = "hard"

# How far back to WALK commit pairs looking for deletions: the pipeline's
# own retention window plus slack, so a deletion commit stays visible to
# this check (and the daily tripwire) for several days after it lands.
# This is a *visibility* window only -- it must never double as the
# *legality* threshold below, which belongs to the pipeline's own
# DIGEST_ITEMS_MAX_AGE_DAYS. (An earlier version reused this constant for
# both, which branded every routine age-out of an item 8-10 days old an
# "illegal deletion": the pipeline prunes at 8 days, so judging legality
# against 10 flags the pipeline's own normal behaviour. See
# fixtures/test_deletion_diff_ageout_boundary.py.)
RETENTION_SLACK_DAYS = 10

# run.py stamps generated_at BEFORE the verify/summarise steps run, and
# merge_digest_window computes its prune cutoff from its own, LATER "now" --
# so an item the pipeline legally pruned can measure a few minutes younger
# than the full retention window when re-measured against generated_at.
# This tolerance absorbs exactly that same-run skew and nothing more: a
# backdated-then-deleted item is hours-to-days inside the window, far
# beyond it.
LEGALITY_SKEW_TOLERANCE = timedelta(hours=1)


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
        return [could_not_run(CHECK_ID, "fewer than 2 commits to data/digest.json since BOOTSTRAP_CUTOFF -- cannot diff yet",
                               bootstrap_expected=True)]

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

    # Wide-window id anchor (see module docstring, hardening 2): the TRUE
    # earliest-ever-recorded first_seen for each id, immune to a nearer-term
    # backdate of the SAME item that a narrower commit-pair walk could
    # otherwise be fooled by.
    id_anchor = earliest_first_seen_by_id(repo_root, since_days=90, after=bootstrap_cutoff)

    findings = []
    for (old_sha, _od), (new_sha, _nd) in zip(commits, commits[1:]):
        old = file_at_commit(repo_root, old_sha, "data/digest.json")
        new = file_at_commit(repo_root, new_sha, "data/digest.json")
        if not old or not new:
            continue
        new_gen = _parse_iso(new.get("generated_at"))
        if not new_gen:
            # An unparseable generated_at must not silently exempt this pair
            # from deletion checking -- that's a gap, not a clean bill.
            findings.append(finding(
                CHECK_ID, "could_not_run",
                f"could not verify deletions between {old_sha[:8]} and {new_sha[:8]}",
                f"{new_sha[:8]}'s generated_at ({new.get('generated_at')!r}) did not parse, so this "
                "commit pair's deletions could not be checked for legality.",
                {"old_sha": old_sha, "new_sha": new_sha},
            ))
            continue

        old_by_key = {}
        old_by_id = {}
        for it in old.get("items", []):
            try:
                key = run_mod._dedupe_key(it)
            except Exception:
                continue
            if key:
                old_by_key[key] = it
            if it.get("id"):
                old_by_id[it["id"]] = it

        new_keys = set()
        new_ids = set()
        for it in new.get("items", []):
            try:
                key = run_mod._dedupe_key(it)
            except Exception:
                continue
            if key:
                new_keys.add(key)
            if it.get("id"):
                new_ids.add(it["id"])

        # Legality is judged by the pipeline's OWN retention rule (read live
        # from run.py so a retention change can't silently re-drift this
        # check), softened only by the same-run clock skew tolerance above.
        # An item at least this old at generated_at is a legitimate age-out.
        retention_days = getattr(run_mod, "DIGEST_ITEMS_MAX_AGE_DAYS", 8)
        cutoff = new_gen - timedelta(days=retention_days) + LEGALITY_SKEW_TOLERANCE
        for key, old_it in old_by_key.items():
            if key in new_keys:
                continue
            if old_it.get("id") and old_it["id"] in new_ids:
                # Same id still present under a different key -- the item's
                # `url` was edited, not deleted. Recorded as info so this
                # isn't silently invisible, but it is not a deletion.
                findings.append(finding(
                    CHECK_ID, "info",
                    f"item's dedupe key changed (url edited, not deleted): '{old_it.get('title', '')[:60]}'",
                    f"id={old_it.get('id')} is present in both {old_sha[:8]} and {new_sha[:8]}, but its "
                    f"dedupe key changed ({key!r} -> a different key) -- the item's url was edited, and "
                    "this is not treated as a deletion.",
                    {"id": old_it.get("id"), "old_key": key, "old_sha": old_sha, "new_sha": new_sha},
                ))
                continue
            # Prefer the full-history id anchor over this pair's own
            # old-commit first_seen -- see module docstring, hardening 2.
            old_fs = _parse_iso(id_anchor.get(old_it.get("id")) or old_it.get("first_seen"))
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
                anchor_note = (
                    f" (id-anchored earliest first_seen; the item's own recorded value in {old_sha[:8]} "
                    f"was {old_it.get('first_seen')!r}, which may itself already be falsified)"
                    if id_anchor.get(old_it.get("id")) and id_anchor.get(old_it.get("id")) != old_it.get("first_seen")
                    else ""
                )
                findings.append(finding(
                    CHECK_ID, "critical" if still_missing else "warn",
                    (f"item deleted while still inside the retention window: '{old_it.get('title', '')[:60]}'"
                     if still_missing else
                     f"item was illegally deleted then restored: '{old_it.get('title', '')[:60]}'"),
                    f"Present in {old_sha[:8]}, absent in {new_sha[:8]} (generated_at={new.get('generated_at')}). "
                    f"Earliest known first_seen={old_fs.isoformat()}{anchor_note} is only "
                    f"{(new_gen - old_fs).days} day(s) before generated_at -- inside the retention "
                    "window, so this is an illegal deletion, not a legitimate age-out."
                    + ("" if still_missing else " The item is present again in the current digest.json -- "
                       "this is a historical record for the ledger, not an open action item."),
                    {"key": key, "id": old_it.get("id"), "old_sha": old_sha, "new_sha": new_sha,
                     "first_seen": old_fs.isoformat(), "recorded_first_seen": old_it.get("first_seen"),
                     "new_generated_at": new.get("generated_at"), "still_missing": still_missing},
                ))

    return findings
