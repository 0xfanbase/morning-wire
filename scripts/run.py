"""Orchestrator for the Reg Radar daily digest pipeline.

Runs fetch -> register diff -> health/heal -> dedupe -> verify -> summarise
-> render, then leaves data/digest.json, data/seen-items.json, data/sources.json
and docs/index.html ready for the workflow to commit. On any failure, exits
non-zero WITHOUT touching digest.json or docs/index.html, so the published
page never regresses to blank -- the workflow step that follows simply finds
nothing new to commit.
"""
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import fetch
import heal
import registers
import render
import summarise
import verify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run")

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "data" / "sources.json"
SEEN_ITEMS_PATH = ROOT / "data" / "seen-items.json"
DIGEST_PATH = ROOT / "data" / "digest.json"

SEEN_ITEMS_MAX_AGE_DAYS = 90
DIGEST_ITEMS_MAX_AGE_DAYS = 8  # a little slack past the "last 7 days" UI label


def _canonical_url(url):
    return (url or "").strip().rstrip("/")


def _dedupe_key(item):
    """Register-diff items (multiple licences added/removed in one run) all
    share the same register page as their display `url` -- keying dedupe on
    url alone would collapse distinct entity events into one. Registers.py
    already assigns each such item a unique, stable `id`; prefer that when
    present and fall back to canonical URL for ordinary fetched items (which
    don't get an `id` until after dedupe runs).
    """
    return item.get("id") or _canonical_url(item.get("url"))


def _title_hash(title):
    return hashlib.sha256((title or "").strip().lower().encode("utf-8")).hexdigest()


def _stable_id(item):
    digest = hashlib.sha1(_canonical_url(item["url"]).encode("utf-8")).hexdigest()[:10]
    source_slug = re.sub(r"[^a-z0-9]+", "-", item["source"].lower()).strip("-")
    return f"{item['jurisdiction'].lower()}-{source_slug}-{digest}"


def _load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def dedupe(raw_items, seen):
    """Split raw fetched/register items into ones worth surfacing this run
    ("new" or material "update"). Pure repeats and non-material edits are
    skipped immediately (but still bump last_seen so we don't re-ask about
    the same cosmetic change every day) since they carry no cap-interaction
    risk either way.

    Returns (surfaced_items, pending_commits). `seen` is NOT mutated for
    "new"/"update" candidates here -- the caller must call the returned
    commit function for each item that actually survives this run's item
    cap. Committing eagerly would mark a cap-dropped item as "seen" and it
    could never resurface on a quieter future run.
    """
    now = datetime.now(timezone.utc).isoformat()
    surfaced = []
    pending_commits = []
    materiality_calls = 0

    # Merge multiple sources reporting the same story in one run (register
    # items use their unique per-entity id; ordinary items use canonical URL).
    by_key = {}
    for item in raw_items:
        by_key.setdefault(_dedupe_key(item), item)

    for key, item in by_key.items():
        title_hash = _title_hash(item["title"])
        prior = seen.get(key)

        if prior is None:
            item["status"] = "new"

            def commit(key=key, title_hash=title_hash, title=item["title"]):
                seen[key] = {"title_hash": title_hash, "first_seen": now, "last_seen": now, "title": title}

            surfaced.append(item)
            pending_commits.append((item, commit))
            continue

        if prior["title_hash"] == title_hash:
            prior["last_seen"] = now  # pure repeat -- keep memory fresh, don't resurface
            continue

        # Same URL, changed title: candidate "update". Gate with a cheap
        # keyword heuristic before spending a Claude call on it.
        material = False
        if summarise.looks_material(prior.get("title", ""), item["title"]):
            material = summarise.judge_material_update(prior.get("title", ""), item, materiality_calls)
            materiality_calls += 1

        if material:
            item["status"] = "update"

            def commit(prior=prior, title_hash=title_hash, title=item["title"]):
                prior.update({"title_hash": title_hash, "last_seen": now, "title": title})

            surfaced.append(item)
            pending_commits.append((item, commit))
        else:
            # Not material -- commit immediately so we don't re-ask Claude
            # tomorrow about the same non-material change.
            prior["title_hash"] = title_hash
            prior["last_seen"] = now

    return surfaced, pending_commits


def prune_seen_items(seen):
    cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_ITEMS_MAX_AGE_DAYS)
    kept = {}
    for key, entry in seen.items():
        try:
            last_seen = datetime.fromisoformat(entry["last_seen"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if last_seen >= cutoff:
            kept[key] = entry
    return kept


def merge_digest_window(previous_items, fresh_items):
    """Union previous items (still within the retention window) with this
    run's new/updated items, letting this run's version win on URL clashes,
    then prune anything past DIGEST_ITEMS_MAX_AGE_DAYS.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=DIGEST_ITEMS_MAX_AGE_DAYS)
    by_key = {}

    for item in previous_items:
        try:
            first_seen = datetime.fromisoformat(item["first_seen"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if first_seen >= cutoff:
            by_key[_dedupe_key(item)] = item

    for item in fresh_items:
        by_key[_dedupe_key(item)] = item

    return list(by_key.values())


FINAL_ITEM_FIELDS = (
    "id", "jurisdiction", "source", "title", "url", "published", "type",
    "priority", "status", "verification", "summary", "so_what", "first_seen",
)


def finalize_item(item):
    return {k: item[k] for k in FINAL_ITEM_FIELDS if k in item}


def main():
    sources = _load_json(SOURCES_PATH, [])
    seen = _load_json(SEEN_ITEMS_PATH, {})
    previous_digest = _load_json(DIGEST_PATH, {"items": []})

    logger.info("1/6 fetch")
    fetch_results = fetch.fetch_all(sources)
    fetched_items = [it for r in fetch_results.values() for it in r["items"]]

    logger.info("2/6 register diff")
    register_items, register_health_notes = registers.run_registers(sources)

    logger.info("3/6 health check + self-heal")
    source_health = heal.health_check_and_heal(sources, fetch_results, register_health_notes)
    SOURCES_PATH.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("4/6 dedupe")
    raw_items = fetched_items + register_items
    now_iso = datetime.now(timezone.utc).isoformat()
    surfaced, pending_commits = dedupe(raw_items, seen)
    for item in surfaced:
        item.setdefault("first_seen", now_iso)
        item.setdefault("id", _stable_id(item))
    surfaced = summarise.select_top(surfaced)

    # Only now commit seen-items memory for items that actually survived the
    # cap -- a cap-dropped item must remain eligible to resurface on a
    # quieter future run instead of being permanently marked "seen" today.
    surviving = {id(it) for it in surfaced}
    for item, commit in pending_commits:
        if id(item) in surviving:
            commit()

    logger.info("5/6 verify (%d candidate items)", len(surfaced))
    verify.verify_items(surfaced)

    logger.info("6/6 summarise (%d candidate items)", len(surfaced))
    summarise_ok, top_of_mind = summarise.summarise_items(surfaced)
    if not summarise_ok:
        # Don't let a failed AI-enrichment call pass silently as if every
        # card's summary/so-what were genuinely Claude-generated -- reuse
        # the existing source-health banner/footer to surface it. The digest
        # still publishes either way (raw titles, official-tier verification,
        # register diffs and dedupe all work without an API key).
        if os.environ.get("ANTHROPIC_API_KEY"):
            note = ("Batch summarisation call failed this run -- cards below show raw "
                    "titles instead of AI summaries. Check the Action logs.")
        else:
            note = ("No ANTHROPIC_API_KEY configured -- AI summaries, corroboration and "
                    "source self-healing are disabled; cards show raw source titles.")
        source_health.append({"name": "Claude summarisation", "status": "dead", "note": note})

    merged_items = merge_digest_window(previous_digest.get("items", []), surfaced)
    digest = {
        "generated_at": now_iso,
        "top_of_mind": top_of_mind,
        "items": [finalize_item(it) for it in merged_items],
        "source_health": source_health,
    }

    # Render before persisting -- a bad render must not corrupt seen-items.json
    # or digest.json, and must never touch the last-good docs/index.html.
    render.render(digest)

    DIGEST_PATH.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")
    SEEN_ITEMS_PATH.write_text(json.dumps(prune_seen_items(seen), indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info(
        "done: %d items in digest, %d surfaced this run, %d sources healthy",
        len(digest["items"]), len(surfaced),
        sum(1 for h in source_health if h["status"] != "dead"),
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("pipeline failed -- leaving last-good digest.json / docs/index.html untouched")
        sys.exit(1)
