# Reg Radar — session guide

This repo publishes a daily digital-asset regulatory digest to GitHub Pages
(`docs/index.html`, generated from `data/digest.json`). The GitHub Action runs the
fetch/dedupe/register-diff pipeline on a cron and publishes even without an
`ANTHROPIC_API_KEY` — but in that keyless mode, cards show raw source headlines instead
of AI summaries, and industry items stay unverified.

## Task: "enrich today's digest"

When asked to enrich the digest (the subscription-based alternative to an API key),
do this:

1. `git pull` the default branch first — the Action commits new data daily.
2. Read `data/digest.json`. Find every item where enrichment is missing: `summary`
   equals `title`, or `so_what` starts with "Review the source directly".
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
     `designation`, `licensing`, `peer_move`, `speech`, `news`.
   - **priority**: `high` for enforcement actions, final rules, sanctions designations,
     licensing grants, and anything material touching HK/mainland China or the topic
     boosts (stablecoins, custody, tokenisation/RWA, prudential treatment of bank
     cryptoasset exposures, sanctions/travel rule, AML/CFT rulemaking); else `normal`.
4. Write the top-level `top_of_mind` field in `data/digest.json`: one or two sentences
   (max ~45 words) saying what is top of mind today for the reader, synthesising the
   day's high-priority items. Plain English, no acronyms, neutral voice. Set it to ""
   on quiet days. The page shows it as a callout above the priority list (which is
   capped at 5 rows — jurisdiction order, then recency).
5. Optional but valuable: for `tier`-industry items whose `verification.level` is
   `single_source`, use web search to look for an official source or a second
   independent reputable outlet (regulator site, Reuters, Bloomberg, FT or equivalent).
   If found, set `verification.level` to `corroborated` and make
   `verification.sources` exactly two entries: the original plus the confirming
   `{name, url}` (http/https URLs only). If nothing confirms it, leave it alone.
6. In `source_health`, if there is a row named `Claude summarisation`, replace it with:
   `{"name": "Claude summarisation", "status": "ok", "note": "Summaries written via
   Claude Code session on <YYYY-MM-DD>"}`.
7. Re-render: `python3 scripts/render.py` (stdlib only — no pip install needed).
8. Commit `data/digest.json` and `docs/index.html` with a `[skip ci]` message and push
   to `main` (if pushing to `main` is blocked, push a branch, open a PR and merge it).
   GitHub Pages serves `docs/` from `main`, so the push IS the publish — the live site
   at https://lockout-fit.github.io/Reg-Radar/ updates a minute or two later. No
   artifact or other publishing step is needed.

Do NOT change the page template (`scripts/templates/page.html`) layout, the schema
field names, or any enum values — `scripts/render.py` validates items and silently
drops any that don't conform.

## House rules

- The published page must stay neutral: no employer names, no byline, no
  bank-specific references anywhere in the repo.
- Never commit secrets. The workflow reads `ANTHROPIC_API_KEY` from repo secrets
  only; keyless operation is fully supported.
- `data/seen-items.json` and `data/registers/` are pipeline memory — don't edit them
  by hand.
