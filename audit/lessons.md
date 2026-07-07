# Lessons

Each entry: LESSON → INVARIANT → EVIDENCE → CHECK → RULE → STATUS.
STATUS flips to `absorbed` only once a check exists that actually fires on
the bad case (a "red fixture") — a lesson written down but not enforced by
code is not yet learned. A lesson left `open` for more than 2 weeks is
itself a finding the next audit run should raise.

Add new lessons only via a reviewed PR (never auto-appended by an
unattended run) — this file is the project's memory of what went wrong and
why, and it must stay trustworthy.

---

## L1 — first_seen backdating + wrongful deletion (2026-07-07)

**LESSON:** A local, "obviously fine" data-repair step can feed a *later*,
unrelated step and cause silent, real harm — reasoning about a mutation in
isolation is not enough; its effect on every downstream consumer of that
field must be checked too.

**INVARIANT:** `item.first_seen` means "the moment this pipeline discovered
the item" — never conflate it with `item.published` (the source's own
timestamp). Documented in README precisely because slow-to-list sources
(Hong Kong's FSTB, the European Banking Authority) publish days before this
pipeline ever ingests them; splitting "Today vs archive" on `published`
would misfile them.

**EVIDENCE:** A "backfill the last 7 days" maintenance step set
`first_seen = published` for every item published before the current day, in
order to spread items across archive days for a demo. This silently evicted
genuinely-discovered-today items (Hong Kong FSTB, 2× European Banking
Authority) from the Digest tab. Worse: the retention-prune step that ran
immediately after read those now-falsified `first_seen` values and deleted
4 real items outright — not misfiled, actually removed from the published
digest, for hours, with no automated check anywhere catching it. It was only
caught because a human noticed "Today" looked oddly Global-only and asked a
diagnostic question. commit: `cd206e3`. A second, more subtle instance of
the *same* root cause was found a few hours later on 2 more items
(Chainalysis, Elliptic) that the first manual repair missed, because they
were added to the digest *after* the git commit used as the "last known
good" restoration point.

**CHECK:** `scripts/audit_checks/check_first_seen_3way.py` (three-way
reconciliation: digest.json vs seen-items.json vs the item's earliest git
appearance — none of the three is trusted alone) and
`check_deletion_diff.py` (every consecutive pair of `data/digest.json` git
commits is diffed; a deletion is legal only if the OLDER commit's own
recorded `first_seen` was already outside the retention window relative to
the NEWER commit's `generated_at` — read from git, never from the possibly-
falsified current value).

**RULE:** Any data-repair recipe that rewrites `first_seen` (or any field a
later pipeline step reads to decide retention/dedupe) must be run through
`python3 scripts/audit.py --simulate` *before* being applied, and the
`--simulate` output pasted into the PR as proof of zero unexpected item
loss. Never trust `data/seen-items.json` as the sole source of truth for
"when was this really first seen" — it is mutable pipeline memory and was
directly hand-edited during this very incident.

**STATUS:** absorbed (both checks exist and fire on the real incident
commit `cd206e3` as their red-fixture case — see
`scripts/audit_checks/fixtures/`).
