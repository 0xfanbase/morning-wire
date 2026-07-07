# Reg Radar

A self-maintaining daily digital-asset regulatory intelligence digest, published as a
static site on GitHub Pages. No framework, no database, no server: one repo, one
scheduled GitHub Action, static HTML output.

The published page is pure static HTML with an embedded JSON data object — there are no
API keys, fetch calls, or third-party requests in the browser. Everything (fetching,
verifying, summarising, rendering) runs server-side in GitHub Actions, so the page is
safe to open from a locked-down corporate device.

**AI-sourced and AI-generated digest. Verify against official sources before acting.**
Automated aggregation — not advice.

## How it works

Each scheduled run (`scripts/run.py`):

1. **Fetch** — pulls RSS/Atom feeds or scrapes listing pages for every source in
   `data/sources.json` (`scripts/fetch.py`).
2. **Register diff** — snapshots official registers (e.g. the SFC's list of licensed
   virtual asset trading platforms) and diffs against the last snapshot in
   `data/registers/`, turning additions/removals into `licensing` items
   (`scripts/registers.py`).
3. **Health check + self-heal** — tracks consecutive failures per source in
   `data/source-health.json`; after 5 consecutive failed/empty runs, asks Claude (with
   the web-search tool) to find and validate a replacement URL, updates
   `data/sources.json` in place, and logs the change to `CHANGELOG-sources.md`. A source
   is never silently dropped — if nothing validates, it's marked `dead` and surfaced in
   the page footer (`scripts/heal.py`).
4. **Dedupe** — matches items by canonical URL against `data/seen-items.json`. A repeat
   with an unchanged title is skipped. A repeat with a *changed* title is only
   resurfaced (as `status: "update"`) if Claude judges the change materially significant
   (a decided enforcement outcome, an adopted final rule, a set penalty/effective date,
   a granted licence) — this keeps Claude calls bounded instead of re-litigating every
   cosmetic edit a feed makes.
5. **Verify** — official-tier items are trusted as-is. Industry-tier items must be
   corroborated by an official source or a second independent reputable outlet (Reuters,
   Bloomberg, FT, or equivalent) via Claude + web search, capped at 10 calls/run; anything
   over the cap (or unconfirmed) is flagged `single_source` (`scripts/verify.py`).
6. **Summarise** — batches up to 25 items into one Claude call for a plain-English
   summary, a practical "so what" for an HK/China-focused digital-asset FCC function, a
   type, and a priority (`scripts/summarise.py`). All Claude JSON responses are parsed
   defensively (fence-stripped, try/except with safe fallbacks).
7. **Render** — writes `data/digest.json` and injects it into the designed template
   (`scripts/templates/page.html`) to produce `docs/index.html`, which GitHub Pages
   serves from `main`/`docs` (`scripts/render.py`). Every item is schema-validated before
   it's embedded in the public page; malformed items are dropped rather than crashing
   the render for everyone else.
8. **Commit** — the workflow commits `data/` and `docs/` back (plain message; the
   Pages deploy listens for this workflow's completion via `workflow_run`, since
   bot-token pushes never fire push-triggered workflows). If any
   step above raises, `run.py` exits non-zero *before* touching `digest.json` or
   `docs/index.html`, so the workflow's commit step finds nothing to commit and the
   published page never regresses to blank.

## Setup

1. **Secret (optional)** — add a repo secret named `ANTHROPIC_API_KEY` (Settings →
   Secrets and variables → Actions) for AI summaries, corroboration and source
   self-healing. **The digest runs fine without it**: fetching, dedupe, register
   diffs, jurisdiction grouping and official-source badges all work keyless; cards
   simply show raw headlines and a banner notes that AI enrichment is off.
   Subscription alternative: skip the key entirely and run a Claude Code session on
   this repo saying "enrich today's digest" — `CLAUDE.md` contains the full recipe
   (the session writes the summaries itself, re-renders and pushes; zero API cost).
2. **Pages** — the repository must be **public** for GitHub Pages on the free plan
   (Settings → General → Danger zone → Change visibility; the repo is deliberately
   neutral, `noindex`ed, and safe to make public). That is the ONLY manual step:
   `.github/workflows/pages.yml` self-enables Pages (`configure-pages` with
   `enablement: true`) and deploys `docs/` on every push to `main`, so no
   Settings → Pages configuration is needed. The site serves at
   `https://<owner>.github.io/<repo>/` (this repo:
   `https://lockout-fit.github.io/Reg-Radar/`) — publicly reachable, no login of any
   kind needed to view it.
3. **Cron** — the workflow (`.github/workflows/digest.yml`) runs `0 23 * * 0-4` UTC,
   i.e. 07:00 HKT Monday–Friday (HKT is UTC+8 with no DST, so 23:00 UTC rolls into the
   next HKT calendar day). It also supports manual runs via the Actions tab
   (`workflow_dispatch`). Two things to expect:
   - GitHub's cron scheduler can drift 5–15 minutes at peak load — the 07:00 target
     leaves headroom before a ~07:15 check.
   - GitHub auto-disables a schedule trigger after 60 days with no commits to the repo.
     This workflow's own daily commit-back keeps it alive; if the repo is ever paused
     for 2+ months, re-enable the schedule manually under Actions → Daily digest.
4. **First run** — trigger `workflow_dispatch` manually once to seed
   `data/digest.json`, `data/seen-items.json`, `data/registers/`, and
   `data/source-health.json`.

## Repo layout

```
/.github/workflows/digest.yml   cron + workflow_dispatch
/scripts/run.py                 orchestrator (fetch -> ... -> render)
/scripts/fetch.py               feed/page fetching + parsing
/scripts/registers.py           official-register snapshot + diff
/scripts/heal.py                dead-source detection + replacement
/scripts/verify.py              corroboration of industry-sourced items
/scripts/summarise.py           Claude API calls (summary/so_what/type/priority)
/scripts/render.py              digest.json -> docs/index.html
/scripts/templates/page.html    the designed page template (DIGEST placeholder)
/data/sources.json               source registry
/data/registers/                 last-known snapshots of official registers
/data/source-health.json         per-source consecutive-failure tracking
/data/seen-items.json            dedupe memory (pruned after 90 days)
/data/digest.json                rolling ~8-day window of published items
/docs/index.html                 published page (GitHub Pages serves this)
/CHANGELOG-sources.md            auto-log of healed/replaced sources
```

## Notes on the design

- **Neutral by construction.** No employer or personal names anywhere in the repo or
  page. `<meta name="robots" content="noindex">` keeps it out of search indexes. The
  disclaimer ("AI-sourced and AI-generated... verify before acting") appears in both the
  header and footer of the page.
- **`first_seen` vs `published`.** Each item carries both the source's own publish
  timestamp (`published`, shown on the card) and the HKT date this pipeline first
  ingested it (`first_seen`, used to decide whether it lands in "Today" or the "Last 7
  days" archive). Splitting on `published` alone would misfile most overnight US/EU
  news — items a US regulator posts in the evening ET land in the small hours HKT and
  are `published` on the *previous* HKT calendar day even on the run that surfaces them
  for the first time.
- **Model.** Summarisation, verification, and self-healing all use
  `claude-sonnet-4-6` via the `anthropic` Python SDK, with the web-search tool for
  verify/heal.
- **Topical filter.** General-mandate regulator feeds (bank supervision, futures,
  securities-at-large) are keyword-filtered to digital-asset-relevant items before
  anything else runs, so a CFTC or Federal Reserve press feed doesn't flood the digest
  with unrelated releases.
- **Known scraping caveats.** FATF's public site 403s a plain HTTP client outright. MAS's
  press-release listing is fully client-rendered (no article markup in the server HTML at
  all), so it's gated with a `href_pattern` requiring an actual `/news/media-releases/...`
  link rather than falling back to nav-menu junk; expect both to sit at 0 relevant items
  most runs, with self-heal retrying each run.
- **Health tracking uses pre-filter item counts.** A source counts as failing only on an
  actual fetch/parse error or zero *raw* items (before the topical relevance filter) --
  a general-mandate feed (OCC, ESMA, DOJ...) having no crypto news on a given day is not
  a failure and won't trigger self-heal.
