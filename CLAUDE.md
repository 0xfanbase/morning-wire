# Digital Assets Morning Wire — session guide

This repo publishes a daily digital-asset regulatory digest to GitHub Pages
(`docs/index.html`, generated from `data/digest.json`). The GitHub Action runs the
fetch/dedupe/register-diff pipeline on a cron and publishes even without an
`ANTHROPIC_API_KEY` — but in that keyless mode, cards show raw source headlines instead
of AI summaries, and industry items stay unverified.

## Task: "enrich today's digest"

When asked to enrich the digest (the subscription-based alternative to an API key),
do this:

1. FIRST make sure the day's pipeline run is finished — enriching mid-run loses
   the race and one side's push fails on a guaranteed docs/index.html conflict:
   use the GitHub MCP actions tools to check the latest "Daily digest" workflow
   run; if it is queued/in_progress, wait for it to conclude (poll every couple
   of minutes, up to ~20). Then `git pull` the default branch.
2. Read `data/digest.json`. Find every item where enrichment is missing: `summary`
   equals `title`, or `so_what` starts with "Review the source directly".
   **If there is nothing to enrich** (weekends, re-runs — the list is empty):
   STOP here. Do not rewrite `top_of_mind`, do not touch `source_health` or
   `run_log`, do not commit or push — a no-op run must leave no trace.
3. For each such item, write the two lines yourself (you are the model — no API call
   is needed):
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
   - **priority**: `high` for enforcement actions, final rules, sanctions designations,
     licensing grants, and anything material touching HK/mainland China or the topic
     boosts (stablecoins, custody, tokenisation/RWA, prudential treatment of bank
     cryptoasset exposures, sanctions/travel rule, AML/CFT rulemaking); else `normal`.
4. Write the top-level `top_of_mind` field in `data/digest.json`: one or two sentences
   (max ~45 words) saying what is top of mind today for the reader, synthesising the
   day's high-priority items. Plain English, no acronyms, neutral voice. Set it to ""
   on quiet days. The page shows it as a callout above the priority list (which is
   capped at 5 rows — jurisdiction order, then recency — and only admits items
   published within the last 7 days, so a stale item marked `high` never headlines).
5. While summarising, capture any EXPLICIT future dates the items mention —
   consultation comment deadlines, rule effective dates, licence application
   windows — into the top-level `radar` list (the page's "On the radar" strip):
   `{"date": "YYYY-MM-DD", "label": "Comments close: <what>", "jurisdiction":
   "HK|CN|US|EU|SG|GLOBAL", "url": "<source url, optional>"}`. Only dates stated
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
   `{"name": "Claude summarisation", "status": "ok", "note": "Summaries written via
   Claude Code session on <YYYY-MM-DD>"}`.
9. Append an entry to the top-level `run_log` list (the page's Audit log tab):
   `{"at": "<now, UTC ISO-8601>", "note": "Enrichment: <N> items summarised and
   classified via Claude Code session"}` — one short sentence; mention corroborations
   if any were made. Keep the list as-is otherwise; the pipeline caps it at 30.
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
