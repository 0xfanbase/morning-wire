# Weekly integrity audit — playbook

This is the runbook a fresh Claude Code session follows every time the weekly
integrity-audit Routine fires. It has no memory of prior runs beyond what is
committed in this repo (`audit/ledger.jsonl`, `audit/lessons.md`,
`audit/baseline.json`, `audit/exceptions.json`) — read those first.

Origin: the 2026-07-07 incident (`audit/lessons.md`, L1) where a local
"obviously fine" data-repair step silently backdated `item.first_seen` and
caused a later step to delete 4 real items from the published digest, with
no automated check catching it for hours. This system exists so the next
version of that mistake gets caught before it reaches the public site.

## Phase 0 — orient

1. Confirm the daily pipeline isn't mid-run: check the latest "Daily digest"
   Actions run via the GitHub MCP tools; if queued/in_progress, wait
   (poll every couple of minutes, up to ~20) rather than racing it.
2. `git pull` the default branch. Read `audit/ledger.jsonl` (tail is enough —
   it's append-only) and `audit/lessons.md` for open lessons and past
   findings, so this run doesn't rediscover something already known and
   accepted, or re-flag something already fixed.

## Phase 1 — detect (ground truth)

Run `python3 scripts/audit.py --json` (see `scripts/audit.py` and
`scripts/audit_checks/`). This is the entire detection surface — do not
hand-roll additional ad-hoc checks against git history; if a gap is found,
the fix is a new check module, not a one-off manual dig (see Phase 6).

The five PROTECTED checks (`first_seen_3way`, `deletion_diff`, `render_drops`,
`docs_feed_parity`, `enum_constant_freeze`) can never be deleted, disabled, or
weakened by this routine — see the never-list below and `audit-guard.yml`,
which blocks this in CI as a backstop.

A `could_not_run` finding on a **hard** check is itself a hard failure, by
design — a guard that cannot run must be as loud as a guard that fails. Early
on (see `base.BOOTSTRAP_CUTOFF`), `first_seen_3way`/`deletion_diff` will
correctly report `could_not_run` ("not enough post-cutoff history yet") —
that is expected, not a bug, until enough real daily commits accumulate.
Don't "fix" this by loosening the cutoff or the check; just note it in the
ledger and move on.

## Phase 2 — triage

For each finding:
- **critical** on a hard/protected check: this is the priority. Investigate
  fully before doing anything else. If it's a live, unresolved data-integrity
  problem (matching the shape of L1), treat it as an incident: reconstruct
  the truth from git history (never from a possibly-poisoned "current" file),
  propose the restoring fix, and follow Phase 4 before opening a PR.
- **warn**: a real but non-open-action-item signal (e.g. `deletion_diff`
  reporting a historical illegal deletion that has since self-corrected).
  Record in the ledger; no code or data change required unless it reveals a
  gap in a check's severity logic.
  Historical dev-era noise from before `BOOTSTRAP_CUTOFF` is common here —
  do not spend the run re-litigating pre-installation history.
- **info**: soft signal (e.g. orphaned register snapshot, near-threshold
  source health). Propose a fix if it's in the permitted whitelist below;
  otherwise just log it.
- **could_not_run** on a soft check: note why in the ledger; fix if trivial
  (e.g. a moved file path), otherwise leave for a human.

## Phase 3 — permitted fixes (safe to make directly, on a branch)

Only these classes of change may be made without extra escalation, and
**always** on a new branch with a PR — never a direct commit to main, and
never a self-merge:

- Doc/code drift wording in `README.md` (e.g. a stale day-count) matching a
  live constant flagged by `check_doc_code_drift` — reword to match the
  constant, never the reverse.
- **Propose** (never perform) deletion of an orphaned `data/registers/*.json`
  snapshot flagged by `check_orphan_registers` — say so in the PR body and
  let a human do the actual `git rm`; this directory is pipeline memory and
  the CLAUDE.md house rule against hand-editing it applies to this routine
  too, no exceptions.
- Updating `audit/enum-snapshot.json` — **only** when the drift it flagged
  corresponds to an already-merged, deliberate code change (i.e. the
  snapshot itself is what's stale, not the code). Never update the snapshot
  to silently wave through an undiscussed enum/constant change.
  `check_enum_constant_freeze` is protected specifically so this can't be
  done quietly — the PR must say which prior commit introduced the change.
- Adding a human-acked entry to `audit/exceptions.json` for a specific,
  already-investigated finding — must have a concrete `expires` date and
  must target one exact finding (check id + evidence key), never a
  wildcard/whole-check suppression. Never add a suppression for a
  PROTECTED check's critical finding.
- Adding a new lessons.md entry (Phase 6) and a new check module (Phase 6).

Everything else — schema changes, `scripts/templates/page.html` layout,
enum/status values, retention windows, dedupe logic, anything under
`data/seen-items.json` — gets a finding and a clearly-written PR proposing
the fix, not a direct edit.

## Phase 4 — simulate before shipping any data-affecting fix

Any fix that touches `first_seen`, retention, or dedupe fields — or restores
data believed wrongly deleted — **must** be run through
`python3 scripts/audit.py --simulate` before the PR is opened, and the output
pasted into the PR body as proof of zero unexpected item loss. This is the
direct lesson from L1 (a "backfill" step that looked locally fine caused
real, silent loss two steps downstream). No exceptions, no "it's obviously
safe."

## Phase 5 — deep-dive rotation

The weekly run can't go equally deep everywhere every time. Rotate through
this coverage map, one area per week, recorded as a `run_log`-style note in
the ledger so the next run picks up the next one (check the last few
`audit/ledger.jsonl` entries for which area was last deep-dived):

1. Source coverage & keyword tuning (are HK/CN/US/EU/SG official sources and
   the tracked institutions still being caught? any new digital-asset
   regulator or bank move that should be added to `data/sources.json`?)
2. UI/UX button-by-button Playwright audit (both themes' light/dark modes,
   mobile + desktop) — only if `scripts/templates/page.html` or its
   JS/CSS changed since the last deep-dive.
3. Dependency freshness in `requirements.txt` vs latest stable releases —
   report only, never auto-upgrade (a version bump is a judgment call for a
   human, since it can silently change API behavior mid-pipeline).
4. Doc/code drift beyond the curated `check_doc_code_drift.py` claim list —
   read README.md and CLAUDE.md fully against current code for anything the
   curated list doesn't cover yet; add a new claim tuple if a gap is found.
5. Schema/enum completeness — any new item shape (type, jurisdiction,
   verification level) worth formally adding, based on a week of real items?

## Phase 6 — self-learn and self-improve

When this run finds a **genuinely new class of failure** (not a recurrence
of something `audit/lessons.md` already covers), write it up in
`audit/lessons.md` using the file's own
LESSON → INVARIANT → EVIDENCE → CHECK → RULE → STATUS format, and open a PR
containing both the lessons.md entry and (this is what makes it real, per
the file's own header) a new or extended check module that actually fires on
the bad case — a red fixture, ideally built from the real evidence the same
way `scripts/audit_checks/fixtures/test_against_incident.py` was built from
the real `cd206e3` incident. STATUS stays `open` until that red fixture
exists and passes; a lesson without a firing check is not yet learned.

Never auto-append to `lessons.md` outside a reviewed PR — that file is this
project's memory of what went wrong and must stay trustworthy.

## Phase 7 — log this run to the public Audit log tab

Every run of this routine must leave a visible trace on the page's Audit log
tab (`data/digest.json`'s `run_log`) -- readers should be able to see that the
weekly integrity audit happened and what it found, the same way they can see
every daily pipeline/enrichment update. This is UNCONDITIONAL: it happens
whether the run found nothing, found something and opened a PR, or found and
fixed a new failure class -- a "no findings this week" run is itself useful
information, not a no-op to hide.

This is different from Phase 3's fixes, which must always go through a PR: a
`run_log` entry is a journal entry about the audit having happened (exactly
analogous to the daily pipeline's own `run_log` appends, or the "enrich
today's digest" recipe's step 9 in CLAUDE.md), not a change to data or
pipeline behavior. It is safe to commit directly to `main`.

1. Write one line summarising the run, matching whichever of these fits:
   - Clean run, nothing to report: `{"at": "<now UTC ISO>", "note": "Weekly
     integrity audit: 11/11 checks ran, no findings — nothing changed."}`
     (use the actual checks-ran count from this run's `scripts/audit.py`
     output, not a hardcoded 11 — a future check being added or removed
     should show up here too).
   - Findings triaged, a fix PR opened: `{"at": "<now>", "note": "Weekly
     integrity audit: found <N> finding(s) (<one-line what>); opened PR #<NN>
     proposing <what>. No other changes."}`
   - A HARD/critical finding needing urgent human attention: `{"at": "<now>",
     "note": "Weekly integrity audit: CRITICAL — <check id> flagged <what>;
     opened PR #<NN> with <the fix>. Needs prompt review."}`
   - A new lesson absorbed: `{"at": "<now>", "note": "Weekly integrity audit:
     identified a new failure class (<what>); added audit/lessons.md entry
     <Lx> and a red-fixture-backed check; opened PR #<NN>."}`
   - `could_not_run` on a protected check due to `BOOTSTRAP_CUTOFF` (expected
     during the project's early weeks): `{"at": "<now>", "note": "Weekly
     integrity audit: <check id> could not run yet (not enough post-cutoff
     history) — expected, not a fault. No other findings."}`
2. RACE GUARD (repeat, don't skip because Phase 0 already checked this once):
   an audit run can take a while, and this step happens at the END of it --
   re-check the latest "Daily digest" workflow run via the GitHub MCP actions
   tools; if it's queued/in_progress, wait for it to conclude (poll every
   couple of minutes) before touching `data/digest.json`, then `git pull`
   `main` again. Committing here mid-pipeline-run risks the exact same
   guaranteed `docs/index.html` push conflict the "enrich today's digest"
   recipe's own Phase-0-equivalent step exists to avoid.
3. Append the entry to `data/digest.json`'s top-level `run_log` (the pipeline
   caps it at 30; do not otherwise touch `run_log`, `source_health`, or
   `top_of_mind`).
4. Re-render: `python3 scripts/render.py`.
5. Commit `data/digest.json`, `docs/index.html` and `docs/feed.xml` directly
   to `main` (plain message, e.g. "chore: log weekly integrity audit run" —
   no `[skip ci]`) and push (if a direct push to main is rejected because the
   daily pipeline landed a commit in the gap, `git pull --rebase` and retry
   once). This is independent of, and does not wait for, any fix PR opened in
   Phase 3 above (that PR lives on its own branch and awaits human review
   regardless of this log entry landing).

## Never-list (hard constraints, no exceptions)

- Never hand-edit or delete anything under `data/registers/` or
  `data/seen-items.json` — propose only, a human acts.
- Never delete, disable, or weaken any check in `PROTECTED_CHECK_IDS`
  (`scripts/audit.py`), and never remove a check from that set.
- Never self-merge a PR this routine opens — a human reviews and merges.
- Never widen a suppression in `audit/exceptions.json` beyond one exact
  finding, and never omit its expiry date.
- Never mark an official-tier, register-kind, or sole-jurisdiction source as
  "accepted dead" in `audit/baseline.json` — `check_jurisdiction_coverage`
  enforces this as a hard guardrail; don't work around it.
- Never touch `scripts/templates/page.html` layout, schema field names, or
  enum values (per the top-level CLAUDE.md instruction) as part of an audit
  fix — that's a product change, not an integrity fix.
- Never bypass `audit-guard.yml` or push directly to main to avoid it.
- Never record a `verification.checked` or fact-check that wasn't actually
  performed (this applies to any digest-content fix made along the way, per
  the daily-enrichment HONESTY RULES in CLAUDE.md).

## PR body template

```
## Audit findings
<one line per finding acted on: check id, severity, one-sentence summary>

## Evidence
<git shas / file diffs / commands run that establish the finding is real>

## Fix
<what changed and why this is the right fix, referencing the specific
INVARIANT or RULE from audit/lessons.md if applicable>

## Simulate output (if this touches first_seen/retention/dedupe)
<paste of `python3 scripts/audit.py --simulate` output>

## Lessons (if a new lesson was added)
<link to the audit/lessons.md diff>

## Never-list check
- [ ] No data/registers/ or data/seen-items.json hand-edits
- [ ] No PROTECTED check weakened/removed
- [ ] No wildcard/expiry-less suppression added
- [ ] No official/register/sole-jurisdiction source marked accepted_dead
- [ ] No page.html schema/enum/layout change
```

## Notes for the routine's own maintenance

If a run finds itself unable to follow this playbook faithfully (e.g. a
Phase 3 fix class turns out to need a judgment call this document doesn't
cover), do not improvise silently — open a PR proposing a specific addition
to this playbook itself, same as any other fix, and flag it clearly in the
PR title so a human notices the process itself is being changed.
