# Digital Assets Morning Wire — session guide

This repo publishes a daily digital-asset regulatory digest to GitHub Pages
(`docs/index.html`, generated from `data/digest.json`). The GitHub Action runs the
fetch/dedupe/register-diff pipeline on a cron and publishes even without an
`ANTHROPIC_API_KEY` — but in that keyless mode, cards show raw source headlines instead
of AI summaries, and industry items stay unverified.

## Task: "enrich today's digest"

When asked to enrich the digest (the subscription-based alternative to an API key),
or when a daily digest-enrichment Routine fires shortly after the pipeline runs, do
this:

1. FIRST make sure the day's pipeline run is finished — enriching mid-run loses
   the race and one side's push fails on a guaranteed docs/index.html conflict:
   use the GitHub MCP actions tools to check the latest "Daily digest" workflow
   run; if it is queued/in_progress, wait for it to conclude (poll every couple
   of minutes, up to ~20). Then `git pull` the default branch. If the GitHub MCP
   tools aren't authenticated in this session, fall back to `git log -1
   --format=%cI` on the default branch as a best-effort proxy for "is a run
   mid-flight", and say plainly in your final report that the Actions check
   was skipped for this reason.
2. Read `data/digest.json`. Find every item where enrichment is missing: `summary`
   equals `title`, or `so_what` starts with "Review the source directly".
   **If there is nothing to enrich** (weekends, re-runs — the list is empty):
   STOP here. Do not rewrite `top_of_mind`, do not touch `source_health` or
   `run_log`, do not commit or push — a no-op run must leave no trace.
   Before enriching, also screen each item for vendor self-promotion: a post
   from an analytics/compliance vendor (Chainalysis, Elliptic, TRM Labs,
   Solidus, Sumsub, Notabene, Onfido, Jumio, and similar) that is primarily a
   product/feature launch, a partnership/integration announcement, or a
   competitive-positioning piece about the vendor's own tooling is marketing,
   not news, and must be REMOVED from `items` rather than enriched — even
   though its source is configured `industry` tier. A genuine industry
   report, crime-trend analysis, or sanctions/regulatory write-up from the
   same vendor is not marketing and should stay. When unsure, keep it and use
   judgment rather than mechanically applying a keyword list. Removing an
   item this way is a legitimate, intended use of this recipe, but
   `scripts/audit_checks/check_deletion_diff.py` cannot tell "deliberate
   editorial removal" from "accidental data loss" by design, and `deletion_diff`
   is a PROTECTED check — `audit/exceptions.json` can NEVER suppress one of its
   `critical` findings (scripts/audit.py enforces this in code, not just as a
   documented rule), so do not try. Expect a real `critical` finding on the
   next `python3 scripts/audit.py` run and on the daily tripwire
   (`.github/workflows/integrity.yml`) for up to `RETENTION_SLACK_DAYS` (~10)
   days, until the removal commit ages out of the diffed window — this is the
   correct, honest signal working as designed, not a bug to silence. Note it
   plainly in this run's commit message and `run_log` entry so a human
   reviewing the eventual tripwire issue has the context, and move on.
3. For each remaining item needing enrichment, write the two lines yourself
   (you are the model — no API call is needed):
   - **summary**: one plain-English sentence describing what happened, readable by any
     compliance team member with no crypto background. No hype, no speculation beyond
     the source.
   - **so_what**: one practical sentence of implication for an HK/China-focused
     digital-asset financial-crime-compliance function at an international bank.
     NEUTRAL VOICE ONLY — never "we"/"our bank"/"your firm", never name or imply any
     specific employer.
   - **No unexplained acronyms** in either line: spell names out in full ("the Hong
     Kong Monetary Authority", not "the HKMA"; "virtual-asset", not "VA"), adding the
     acronym in parentheses only when it is the commonly used name (e.g. "the
     Financial Action Task Force (FATF)").
   - **type**: exactly one of `enforcement`, `final_rule`, `consultation`, `guidance`,
     `designation`, `licensing`, `peer_move`, `speech`, `news`. Use `peer_move` for
     digital-asset initiatives by banks, exchanges and financial institutions (HKEX,
     DBS, HSBC, Standard Chartered, OCBC, Mox Bank, custody/tokenisation launches,
     exchange product moves) — the page shows these under "Industry intel".
   - **jurisdiction**: correct it away from the source's default when the STORY is
     clearly about one specific jurisdiction's regulator/agency action, even if the
     reporting source itself is configured `GLOBAL` in `data/sources.json` (e.g. an
     industry blog's write-up of an OFAC sanctions action is US news, not global
     news; a MiCA explainer is EU news; an FCA rulebook explainer is UK news).
     `scripts/audit_checks/
     check_item_jurisdiction_signal.py` flags likely mistags as a soft, `info`-level
     nudge each run — treat its suggestions as a prompt for judgment, never
     auto-apply them mechanically, and never invent a jurisdiction the story doesn't
     support.
   - **priority**: `high` for enforcement actions, final rules, sanctions designations,
     licensing grants, and anything material touching HK/mainland China or the topic
     boosts (stablecoins, custody, tokenisation/RWA, prudential treatment of bank
     cryptoasset exposures, sanctions/travel rule, AML/CFT rulemaking); else `normal`.
     This is the mechanism that keeps the page's Hong Kong/mainland China focus real
     without hardcoding it: HK/CN items get an extra path to `high` on top of the
     type-based rules everyone gets, but a sufficiently material US/UK/EU/SG/global
     story (e.g. a major sanctions action or final rule) still earns `high` on its
     own terms — the reader-lens bias is additive, never exclusionary.
4. Write the top-level `top_of_mind` field in `data/digest.json`: one or two sentences
   (max ~45 words) saying what is top of mind today for the reader, synthesising the
   day's high-priority items. Write for a compliance officer at a globally
   systemically important bank (a G-SIB) — this shapes what you pick and how you
   phrase it, but never say so on the page itself. Rank by direct operational impact
   on a bank compliance function, not simply by the `high` flag: a sanctions
   designation or an enforcement action (something that changes a screening list or
   sets an enforcement precedent) outranks a licensing grant to a single firm, which
   outranks a consultation or a peer bank's product launch. When several high-priority
   items compete for the ~45-word budget, keep the ones with a concrete compliance
   action attached (a list to update, a control to check, a filing deadline) and drop
   pure market-price moves or single-firm news even if flagged `high`. Fact-check
   before writing: every claim in `top_of_mind` must trace back to an item's own
   `summary`/`so_what` already in this digest — never generalise, extrapolate, or add
   an implication the source items don't themselves support. Plain English, no
   acronyms, neutral voice. Set it to "" on quiet days. The page shows it as a callout
   beside the priority list (capped at 3 rows, ranked by compliance materiality — a
   structural regulatory change, e.g. a finalised rule or a sanctions/entity
   designation, generally outranks a routine single-defendant enforcement case, unless
   that enforcement action is itself sanctions/systemic in scale — then recency as a
   tiebreak). Both callouts only render on the exact, unfiltered Today view: they
   disappear the instant any filter — range, jurisdiction, or category — moves off
   Today/All/All, since a stale item marked `high`, or a "top 3" computed over a
   narrowed slice, would no longer mean what it claims to mean.
5. While summarising, capture any EXPLICIT future dates the items mention —
   consultation comment deadlines, rule effective dates, licence application
   windows — into the top-level `radar` list (the page's "On the radar" strip):
   `{"date": "YYYY-MM-DD", "label": "Comments close: <what>", "jurisdiction":
   "HK|CN|US|UK|EU|SG|GLOBAL", "url": "<source url, optional>"}`. Only dates stated
   in the source — never inferred. Keep at most ~6 rows, nearest first; leave
   existing rows alone (the renderer auto-drops past dates).
6. Optional but valuable: for `tier`-industry items whose `verification.level` is
   `single_source`, use web search to look for an official source or a second
   independent reputable outlet (regulator site, Reuters, Bloomberg, FT or equivalent).
   If found, set `verification.level` to `corroborated` and make
   `verification.sources` exactly two entries: the original plus the confirming
   `{name, url}` (http/https URLs only). If nothing confirms it, leave it alone.
7. Date & fact-check pass (HONESTY RULES apply — never record a check that was
   not actually performed):
   - For every item whose `date_source` is `"fetch_time"` (its listing page had
     no date, so the pipeline stamped ingestion time): open the article URL and
     read the real publication date — prefer structured data (JSON-LD
     `datePublished`, `article:published_time`, `<time datetime>`) over loose
     text, which often belongs to event promos rather than the article. If a
     trustworthy date is found, set `published` to it (date-only values →
     midnight `+08:00`) and set `date_source` to `"verified"`. If not found,
     leave both fields alone — never guess a date.
   - For high-priority items: fetch the named official source (the regulator's
     own notice, register or press release) and check the central claim —
     numbers, entities, dates. Only if it actually matches, record:
     `verification.checked = {"at": "<now UTC ISO>", "against": "<official body
     — which document>", "url": "<official url>", "note": "<one line: what was
     matched>"}`. The page renders this as "✓ Checked against …". Where the
     existing corroboration rule fits, also upgrade `verification.level` to
     `corroborated` with the official source as the second entry. If the
     official source CONTRADICTS the item, fix the summary or lower the
     priority — do not record a check.
8. In `source_health`, if there is a row named `Claude summarisation`, replace it with:
   `{"name": "Claude summarisation", "status": "ok", "note": "<Model> — Claude Code
   session on <YYYY-MM-DD>"}`. Model-choice rule (remember this going forward):
   default to **Sonnet** for this recipe — it's routine summarisation/classification
   work. Escalate to **Opus** only when today's run is genuinely harder than usual:
   an unusually large enrichment backlog (rough guide: 15+ items in one pass), several
   items needing careful fact-checking or corroboration judgment calls, or enrichment
   bundled with a larger structural/code change in the same session. State whichever
   model tier was actually used — never write a model name that didn't do the work.
9. Append an entry to the top-level `run_log` list (the page's Audit log tab):
   `{"at": "<now, UTC ISO-8601>", "note": "Enrichment: <N> items summarised and
   classified via Claude Code session"}`; mention corroborations if any were made.
   Keep the list as-is otherwise. Every entry (from this recipe, the daily pipeline,
   or the weekly audit) must follow the **Audit log style rule**:
   - At most 2 complete sentences. Never rely on truncation to fit — write it short.
   - No forensic detail: never name the mechanism/root cause of a bug, an internal
     incident name, or which specific items were affected by a correction. Say WHAT
     changed in general terms ("a data display issue", "a source URL") never HOW or
     WHY — this is a public, unauthenticated page, not an incident postmortem. The
     full story belongs in `audit/lessons.md` (internal), never here.
   - Vague is fine; false is never fine.
   `scripts/render.py` keeps only the most recent 10 entries and truncates
   gracefully (word boundary + ellipsis) as a backstop — that is not a substitute
   for writing a short, complete entry in the first place.
10. Re-render: `python3 scripts/render.py` (stdlib only — no pip install needed). This
   also refreshes `docs/feed.xml` (the RSS feed) and the page's Open Graph tags.
11. Commit `data/digest.json`, `docs/index.html` and `docs/feed.xml` (plain message,
   e.g. "chore: enrich digest" — do NOT add `[skip ci]`, the push must trigger the
   Pages deploy workflow) and push to `main` (if pushing to `main` is blocked, push a
   branch, open a PR and merge it). The push triggers `.github/workflows/pages.yml`,
   which publishes `docs/` to https://fandamentals.github.io/morning-wire/ a minute or
   two later. No artifact or other publishing step is needed. Belt-and-braces:
   afterwards, use the GitHub MCP actions tools to confirm a "Deploy site to Pages"
   run started for your commit; if it didn't, dispatch `pages.yml` on `main` via
   `actions_run_trigger`.

Do NOT change the page template (`scripts/templates/page.html`) layout, the schema
field names, or any enum values — `scripts/render.py` validates items and silently
drops any that don't conform.

## Task: "weekly integrity audit"

When a weekly integrity-audit Routine fires (or a human asks to run/continue the
audit), follow `audit/PLAYBOOK.md` in full — it is the canonical, self-contained
runbook (phases, the permitted-fix whitelist, the never-list, the PR body template,
and the deep-dive rotation) and takes precedence over any summary here. In short:
run `python3 scripts/audit.py`, triage findings against `audit/lessons.md` and
`audit/ledger.jsonl`, propose fixes only within the permitted whitelist on a branch
+ PR (never a direct commit, never a self-merge), run `--simulate` before any
data-affecting fix, and write up any genuinely new failure class in
`audit/lessons.md` with a red-fixture-backed check before marking it `absorbed`.
Never hand-edit `data/registers/` or `data/seen-items.json`, and never remove,
disable, or weaken a check in `scripts/audit.py`'s `PROTECTED_CHECK_IDS`.

## House rules

- The published page must stay neutral: no employer names, no byline, no
  bank-specific references anywhere in the repo.
- Never commit secrets. The workflow reads `ANTHROPIC_API_KEY` from repo secrets
  only; keyless operation is fully supported.
- `data/seen-items.json` and `data/registers/` are pipeline memory — don't edit them
  by hand.
