"""PROTECTED CORE. Catches the exact shape of the 2026-07-07 incident: a
data-repair step silently backdating item.first_seen away from the moment
the pipeline actually discovered it.

first_seen means "when THIS PIPELINE discovered the item" (README, run.py) --
never conflate it with `published` (the source's own date). The incident
happened because a repair script set first_seen = published for older items,
which (a) evicted genuinely-today discoveries from the Digest tab and (b) fed
a later retention prune that read the falsified date and deleted 4 items
outright.

This check reconciles THREE independent records of "when was this first
seen", none of which trusts the others:
  1. digest.json's own first_seen (what's published right now)
  2. data/seen-items.json's first_seen (the pipeline's live dedupe memory --
     but this is MUTABLE and WAS hand-edited during the incident, so it is
     corroborating evidence, never the sole oracle)
  3. the first_seen value AS RECORDED in the EARLIEST git commit that ever
     contained this item -- the one value nothing after the fact can
     rewrite, because git history is append-only

A backward move against the git anchor, with no legitimate 'update' status
explaining a resurfacing, is CRITICAL: it is precisely the incident pattern.

Anchored primarily on item `id` (base.earliest_first_seen_by_id), not the
dedupe key alone (a URL for ordinary items) -- see audit/lessons.md L2. `id`
is assigned once and never reassigned, so it survives a `url` edit; the
dedupe key does not, and a check that anchors on it alone can be orphaned
from an item's real history by nothing more than an edited link. The
key-based anchor is kept as a fallback for the (should not happen in
practice) case of an item with no `id`.
"""
import json
from datetime import datetime, timedelta, timezone

from base import commits_touching, earliest_first_seen_by_id, file_at_commit, finding, could_not_run, BOOTSTRAP_CUTOFF

CHECK_ID = "first_seen_3way"
MODE = "hard"

# A genuine "update" (run.dedupe(): same URL, materially changed title) only
# ever moves an item's first_seen FORWARD, to the run that noticed the
# change -- never backward. Blanket-exempting status="update" from the
# backdating check (as this check originally did) would let a manipulated
# item dodge detection just by carrying that status; the tolerance below
# still allows same-day clock/dedupe timing noise without opening that gap
# back up for anything beyond it (see audit/lessons.md L2, item 5).
UPDATE_BACKDATE_TOLERANCE = timedelta(hours=6)


def _parse(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run(repo_root, bootstrap_cutoff=BOOTSTRAP_CUTOFF):
    """bootstrap_cutoff is overridable so tests can validate detection logic
    against a historical worktree on its own terms; production callers (the
    harness) always use the default -- see base.BOOTSTRAP_CUTOFF."""
    try:
        import sys
        sys.path.insert(0, str(repo_root / "scripts"))
        import run as run_mod  # noqa: the real pipeline's own dedupe-key logic
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import scripts/run.py: {exc}")]

    try:
        commits = commits_touching(repo_root, "data/digest.json", since_days=30, after=bootstrap_cutoff)
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"git history unavailable: {exc}")]
    if len(commits) < 1:
        return [could_not_run(CHECK_ID, "no git history for data/digest.json since BOOTSTRAP_CUTOFF",
                               bootstrap_expected=True)]

    # Anchor: the first_seen value as recorded at each key's EARLIEST git
    # appearance. Walk oldest to newest, keep only the first sighting.
    anchor = {}
    for sha, _date in commits:
        snap = file_at_commit(repo_root, sha, "data/digest.json")
        if not snap:
            continue
        for it in snap.get("items", []):
            try:
                key = run_mod._dedupe_key(it)
            except Exception:
                continue
            if key and key not in anchor:
                anchor[key] = it.get("first_seen")

    anchor_by_id = earliest_first_seen_by_id(repo_root, since_days=90, after=bootstrap_cutoff)

    try:
        current = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
        seen = json.loads((repo_root / "data" / "seen-items.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/digest.json or seen-items.json: {exc}")]

    findings = []
    for it in current.get("items", []):
        try:
            key = run_mod._dedupe_key(it)
        except Exception:
            continue
        cur_fs = it.get("first_seen")
        if not cur_fs:
            continue
        is_update = it.get("status") == "update"

        # Prefer the id-anchored value: it survives a `url` edit, the
        # dedupe-key-based `anchor` does not (see module docstring / L2).
        anc_fs = anchor_by_id.get(it.get("id")) or anchor.get(key)
        if anc_fs and cur_fs < anc_fs:
            cur_dt, anc_dt = _parse(cur_fs), _parse(anc_fs)
            backdate_amount = (anc_dt - cur_dt) if (cur_dt and anc_dt) else None
            # A genuine update's first_seen only ever moves forward (see
            # UPDATE_BACKDATE_TOLERANCE above) -- status="update" excuses a
            # small backward move (same-run timing noise), never a large one.
            excused = is_update and backdate_amount is not None and backdate_amount <= UPDATE_BACKDATE_TOLERANCE
            if not excused:
                findings.append(finding(
                    CHECK_ID, "critical",
                    f"first_seen backdated for '{it.get('title', '')[:60]}'",
                    f"digest.json shows first_seen={cur_fs}, but the earliest git commit that ever "
                    f"contained this item recorded first_seen={anc_fs}. "
                    + (f"status='update' but the backward move ({backdate_amount}) exceeds the "
                       f"{UPDATE_BACKDATE_TOLERANCE} tolerance a genuine same-run update can explain."
                       if is_update else
                       "A backward move with no status='update' signature is the exact pattern of "
                       "the 2026-07-07 incident."),
                    {"id": it.get("id"), "key": key, "current_first_seen": cur_fs, "git_anchor_first_seen": anc_fs,
                     "status": it.get("status")},
                ))

        s = seen.get(key)
        if s and s.get("first_seen") and cur_fs[:16] != s["first_seen"][:16] and not is_update:
            findings.append(finding(
                CHECK_ID, "critical",
                f"first_seen disagrees with seen-items.json for '{it.get('title', '')[:60]}'",
                f"digest.json first_seen={cur_fs} but seen-items.json (the pipeline's live dedupe "
                f"memory) records first_seen={s['first_seen']} for the same key.",
                {"id": it.get("id"), "key": key, "digest_first_seen": cur_fs, "seen_items_first_seen": s["first_seen"]},
            ))

    return findings
