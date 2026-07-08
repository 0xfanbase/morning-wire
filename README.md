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
   the Source health tab (`scripts/heal.py`).
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
2. **Pages** — two one-time manual steps, both free:
   1. The repository must be **public** for GitHub Pages on the free plan
      (Settings → General → Danger zone → Change visibility; the repo is deliberately
      neutral, `noindex`ed, and safe to make public).
   2. Enable Pages once: Settings → Pages → under "Build and deployment", set
      **Source: GitHub Actions**. (The workflow token cannot create the Pages site
      itself — the REST endpoint needs repo-admin rights that `GITHUB_TOKEN` never
      gets, so `configure-pages`' `enablement: true` alone is not enough.)

   After that, `.github/workflows/pages.yml` deploys `docs/` automatically after
   every daily digest run and on every push to `main`. The site serves at
   `https://<owner>.github.io/<repo>/` (this repo:
   `https://fandamentals.github.io/morning-wire/`) — publicly reachable, no login of
   any kind needed to view it.
3. **Cron** — the workflow (`.github/workflows/digest.yml`) runs `0 21 * * 0-4` UTC,
   i.e. 05:00 HKT Monday–Friday (HKT is UTC+8 with no DST, so 21:00 UTC rolls into the
   next HKT calendar day). It also supports manual runs via the Actions tab
   (`workflow_dispatch`). Two things to expect:
   - GitHub's cron scheduler can drift 5–15 minutes at peak load — the 05:00 target
     leaves headroom before a ~05:15 check.
   - GitHub auto-disables a schedule trigger after 60 days with no commits to the repo.
     This workflow's own daily commit-back keeps it alive; if the repo is ever paused
     for 2+ months, re-enable the schedule manually under Actions → Daily digest.
4. **First run** — trigger `workflow_dispatch` manually once to seed
   `data/digest.json`, `data/seen-items.json`, `data/registers/`, and
   `data/source-health.json`.
5. **Branch protection (optional, repo-admin only)** — `CODEOWNERS` names
   required reviewers for the audit system's own files, but GitHub only
   enforces it once branch protection is turned on for `main` with "Require a
   pull request before merging" + "Require review from Code Owners" checked
   (Settings → Branches → Add rule). No workflow or bot token can enable this
   from the outside — it's a one-time human, repo-admin action.

## Repo layout

```
/.github/workflows/digest.yml   cron + workflow_dispatch (the daily pipeline)
/.github/workflows/pages.yml    deploys docs/ to Pages (workflow_run on "Daily
                                digest" + push to docs/** + manual dispatch)
/.github/workflows/integrity.yml    daily report-only tripwire (HARD checks only)
/.github/workflows/audit-guard.yml  CI guard: blocks weakening a PROTECTED check
/scripts/run.py                 orchestrator (fetch -> ... -> render)
/scripts/fetch.py               feed/page fetching + parsing
/scripts/registers.py           official-register snapshot + diff
/scripts/heal.py                dead-source detection + replacement
/scripts/verify.py              corroboration of industry-sourced items
/scripts/summarise.py           Claude API calls (summary/so_what/type/priority)
/scripts/render.py              digest.json -> docs/index.html
/scripts/templates/page.html    the designed page template (DIGEST placeholder)
/scripts/audit.py               weekly/daily integrity-audit harness
/scripts/audit_checks/          one module per check (auto-discovered by audit.py)
/data/sources.json               source registry
/data/registers/                 last-known snapshots of official registers
/data/source-health.json         per-source consecutive-failure tracking
/data/seen-items.json            dedupe memory (pruned after 90 days)
/data/digest.json                rolling ~8-day window of published items
/docs/index.html                 published page (GitHub Pages serves this)
/audit/PLAYBOOK.md              runbook for the weekly integrity-audit routine
/audit/lessons.md               LESSON/INVARIANT/CHECK log of past incidents
/audit/ledger.jsonl             append-only history of audit runs
/audit/exceptions.json          scoped, expiring suppressions of specific findings
/CODEOWNERS                     required-reviewer map (needs branch protection
                                enabled to actually enforce — see Setup)
/CHANGELOG-sources.md            auto-log of healed/replaced sources
```

## Notes on the design

- **Neutral by construction.** No employer or personal names anywhere in the repo or
  page. `<meta name="robots" content="noindex">` keeps it out of search indexes. The
  disclaimer ("AI-sourced and AI-generated... verify before acting") appears in both the
  header and footer of the page.
- **`first_seen` vs `published`.** Each item carries both the source's own publish
  timestamp (`published`, shown on the card) and the HKT date this pipeline first
  ingested it (`first_seen`, used to decide which Range-selector bucket — Today, Last 7
  days, Last 30 days — an item falls into). Splitting on `published` alone would misfile most overnight US/EU
  news — items a US regulator posts in the evening ET land in the small hours HKT and
  are `published` on the *previous* HKT calendar day even on the run that surfaces them
  for the first time.
- **Model.** Summarisation, verification, and self-healing all use
  `claude-sonnet-4-6` via the `anthropic` Python SDK, with the web-search tool for
  verify/heal. The keyless "enrich today's digest" recipe (a Claude Code session
  standing in for the API call) defaults to Sonnet for the same reason — it's
  routine summarisation/classification — and escalates to Opus only for a
  genuinely harder-than-usual day (a large backlog, several corroboration
  judgment calls, or enrichment bundled with a structural change). Whichever
  tier actually did the work is disclosed in the Source health tab's "Claude
  summarisation" row, dated to the day it ran.
- **Topical filter.** General-mandate regulator feeds (bank supervision, futures,
  securities-at-large) are keyword-filtered to digital-asset-relevant items before
  anything else runs, so a CFTC or Federal Reserve press feed doesn't flood the digest
  with unrelated releases.
- **Backlog cap.** Items published more than 7 days ago are ignored at ingest
  (`MAX_ITEM_AGE_DAYS` in `scripts/fetch.py`, aligned with the priority strip's
  own 7-day window) — feeds return their most recent N
  entries regardless of age, so a source's first-ever run (or a newly added source)
  would otherwise flood a *daily* digest with months-old items presented as new. The
  page's priority strip is stricter still: it only admits items published within the
  last 7 days, each row showing its publication date.
- **No mechanical historical backfill.** The digest fills in gradually from each
  day's real fetch, not by reaching backwards for a month of history on day one:
  RSS/Atom feeds only expose their current recent-items window (typically the last
  handful of entries), not an arbitrary date range, so there is no live source to
  backfill 30 days of genuine history *from*. A "Today"/"Last 7 days"/"Last 30
  days"/"All" range selector is provided so the reader's own view grows as real
  runs accumulate. (This also follows directly from `audit/lessons.md` L1: a prior
  attempt to backdate `item.first_seen` to simulate a fuller archive silently
  deleted real items downstream — retention and range bucketing are keyed off
  `first_seen`, the moment this pipeline actually discovered the item, and that
  field is never rewritten to manufacture history.)
- **Vendor-marketing filtering.** An industry-tier source (Chainalysis, Elliptic,
  TRM Labs, and similar analytics/compliance vendors) is monitored for its genuine
  news and analysis, not its own marketing. The "enrich today's digest" recipe in
  `CLAUDE.md` screens out product launches, feature/partnership announcements, and
  competitive-positioning posts about the vendor's own tooling before they'd
  otherwise be enriched and published.
- **Jurisdiction reflects the story, not just the source.** A `GLOBAL`-tier
  industry source (e.g. a compliance-vendor blog) covering one specific
  jurisdiction's regulatory action (an OFAC sanctions list update, a MiCA rule)
  is tagged with that jurisdiction, not folded into Global by default.
  `scripts/audit_checks/check_item_jurisdiction_signal.py` flags likely
  mistags for human/enrichment-session judgment; it never rewrites a tag itself.
- **Known scraping caveats.** FATF's public site 403s a plain HTTP client outright. MAS's
  press-release listing is fully client-rendered (no article markup in the server HTML at
  all), so it's gated with a `href_pattern` requiring an actual `/news/media-releases/...`
  link rather than falling back to nav-menu junk; expect both to sit at 0 relevant items
  most runs, with self-heal retrying each run.
- **Health tracking uses pre-filter item counts.** A source counts as failing only on an
  actual fetch/parse error or zero *raw* items (before the topical relevance filter) --
  a general-mandate feed (OCC, ESMA, DOJ...) having no crypto news on a given day is not
  a failure and won't trigger self-heal.
