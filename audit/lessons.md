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

---

## L2 — the protected checks trust mutable identity and incomplete history (2026-07-07)

**LESSON:** A four-round independent audit (three different reviewers, correctness/
data-integrity/security angles) found that `deletion_diff` and `first_seen_3way` —
the two checks written specifically to catch L1-style silent loss — can themselves
be evaded by a sequence of edits that doesn't look anomalous to either check in
isolation. A check that verifies "this specific mutation looks legal" is not the
same as a check that verifies "no in-window item can ever silently disappear" —
the gap between those two claims is exactly where a determined (or just unlucky)
sequence of edits can slip through.

**INVARIANT (violated):** Every item that is genuinely inside the retention window
must be traceable across any sequence of edits to `data/digest.json`, regardless
of what mutable fields (`url`, `first_seen`, `status`) change along the way.

**EVIDENCE (all found by inspection, not by exploiting the live site):**
1. `_dedupe_key` (`scripts/run.py`) is the canonical URL for ordinary items.
   `check_first_seen_3way.py` and `check_deletion_diff.py` both index by this key.
   Editing an item's `url` gives it a *new* key with no git anchor and no
   `seen-items.json` entry — the backdating-detection branches in both checks are
   skipped entirely for that key, because as far as either check can tell, it's a
   brand-new item that simply appeared with whatever `first_seen` it was given.
2. A two-commit sequence evades both checks even without touching `url`: commit A
   backdates `first_seen` (accepted by `first_seen_3way` unless the weekly audit
   happens to run between commits A and B); commit B deletes the item —
   `deletion_diff` reads commit A's now-falsified `first_seen`, sees it outside the
   retention cutoff, and rules the deletion a legal age-out. This is the *same*
   root cause as L1, just compressed into a sequence a single audit run can miss
   if its timing is unlucky.
3. `check_deletion_diff.py` skips a commit pair entirely if the newer commit's
   `generated_at` doesn't parse — an unparseable timestamp doesn't fail loud, it
   silently exempts that pair from deletion checking.
4. `check_first_seen_3way.py` only iterates the *current* item list — an item
   deleted between two historical commits is never re-examined by this check at
   all (that's `deletion_diff`'s job, which has its own gap above).
5. An item with `status: "update"` is exempt from both of `first_seen_3way`'s
   backdating-detection branches.

**CHECK:** `scripts/audit_checks/fixtures/test_l2_mutable_key_evasion.py` — a
synthetic red/green fixture (there is no real historical incident for L2, this
was found by inspection before it could cause one) built the same way
`test_against_incident.py` was built from the real `cd206e3` incident:
- `test_backdate_then_delete_evasion`: url edit + backdate, then delete —
  proven to evade the ORIGINAL code (verified by hand against a copy of the
  pre-fix checks) and proven caught by the fixed code (gaps 1, 2, 3).
- `test_url_edit_alone_is_not_a_false_deletion`: a legitimate url fix with no
  backdating is correctly NOT reported as a deletion (green case for gap 1's
  fix).
- `test_update_status_no_longer_excuses_large_backdate` /
  `test_update_status_small_backdate_still_excused`: gap 5's fix closes the
  large-backdate bypass while still tolerating small, same-run timing noise.

**RULE (implemented):**
1. `base.earliest_first_seen_by_id` anchors on item `id` (assigned once in
   `run.py`, never reassigned — unlike the dedupe key, recomputed from the
   CURRENT `url` every time). Both `first_seen_3way` (backdating check) and
   `deletion_diff` (legality-of-deletion check) now prefer this id-anchored,
   full-history value over the dedupe-key-based one. This closes gaps 1, 2,
   and 4 together: a `url` edit no longer orphans an item from its history,
   and a deletion's legality is judged against the TRUE earliest-ever-recorded
   first_seen, not just the immediately preceding (possibly already-falsified)
   commit's value.
2. `deletion_diff` now also matches old-vs-new items by `id`: if the same id
   is present in the newer commit under a different key, that's a `url` edit,
   recorded as an `info` finding, never mistaken for a deletion.
3. `deletion_diff` reports an unparseable `generated_at` as `could_not_run`
   (loud) instead of silently skipping that commit pair.
4. `first_seen_3way`'s `status="update"` exemption is now bounded by
   `UPDATE_BACKDATE_TOLERANCE` (6 hours) instead of excusing any backward
   move unconditionally — a genuine update only ever moves first_seen
   forward, so a large backward move can no longer hide behind that status.

**STATUS:** absorbed (all five gaps have a red-fixture-backed check; verified
against `test_against_incident.py`'s original red/green fixture too, with no
regression).

---

## L3 — pipeline defensive gaps found by the same audit round (2026-07-07)

**LESSON:** Several smaller, non-PROTECTED-check-related robustness gaps surfaced
in the same four-round review. None have caused a known incident; recorded here
so they aren't silently rediscovered, and so a future session has a concrete list
to work from rather than needing to re-run the same audit from scratch.

**EVIDENCE / STATUS per item:**
- **Fixed:** `scripts/run.py`'s `merge_digest_window`/`prune_seen_items` used to
  silently discard any item/entry whose `first_seen`/`last_seen` fails to parse.
  Both now log a warning naming the id/key before dropping it, so a malformed
  timestamp leaves a trace in the Action logs instead of quiet, untraceable loss.
  No dedicated red fixture (this is a logging change, not a detection-logic
  change) — verified by reading the diff and confirming the normal
  `--simulate`/full-audit paths still report zero unexpected loss.
- **Fixed:** `scripts/audit.py --simulate` used to exit 0 even when
  `sanitize_digest` itself raised inside the simulation (a broad exception
  handler fell back to treating the pre-sanitize id set as the answer, which
  is the exact "mandatory proof" step L1's RULE requires — it must never be
  able to report success on a crash). Now a crash is tracked separately
  (`sanitize_crashed`), printed as an explicit FAILED line saying the output
  is not proof of anything, and forces a non-zero exit. Verified: the normal
  (non-crashing) path still prints "zero unexpected item loss" and exits 0.
- **Still open:** `verify.py`'s corroboration prompt embeds attacker-
  influenceable `title`/`summary` text verbatim into a Claude prompt with
  web-search access; a crafted feed item could attempt to induce a false
  "confirmed" corroboration. The same-publisher domain check was hardened
  this session (subdomain relationships now correctly detected, closing the
  most likely FALSE-corroboration vector), but the underlying
  "attacker-influenced content reaches a prompt that can mint a public trust
  badge" shape remains and deserves a closer look (e.g. requiring the
  confirming source to be independently web-searched for, never merely
  accepted from the same response that read the original item).
- **Still open:** `scripts/heal.py` validates a candidate replacement source
  on liveness alone (an actual fetch succeeds) for feed-kind sources; only
  page-kind sources with a selector get a relevance check. A live-but-wrong
  feed could self-heal in undetected.
- **Still open, low priority:** `.github/workflows/*.yml` pin third-party
  actions (`actions/checkout`, `actions/setup-python`, etc.) to mutable
  major-version tags, not commit SHAs — standard supply-chain hardening
  advice, low urgency here since these are all first-party GitHub actions,
  but worth doing eventually.

**CHECK:** none of the still-open items have a red-fixture-backed check yet.

**RULE:** none yet for the still-open items — these remain findings to
triage, not absorbed rules. A future session (this or the next weekly audit)
should pick one, build the red-fixture check, and move it into its own
lesson with STATUS `absorbed`.

**STATUS:** partially absorbed — 2 of 5 items fixed and verified (no
detection-logic red fixture needed for either, per the reasoning above); the
remaining 3 (verify.py prompt-injection design, heal.py relevance validation,
workflow SHA pinning) are still open.
