"""Dead-source detection + self-healing.

A source is considered dead if it errors or returns zero items for 5
consecutive runs. On death, Claude (with web_search) is asked to find the
current official equivalent URL; the candidate is validated by actually
fetching it before sources.json is updated. A source is never silently
dropped -- if no replacement validates, it is marked "dead" and surfaces in
the page footer.
"""
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from fetch import fetch_source, is_relevant
from registers import _extract_entities, _get
from summarise import get_client, MODEL, WEB_SEARCH_TYPE, _extract_json_object, _extract_text

logger = logging.getLogger(__name__)

HEALTH_PATH = Path(__file__).resolve().parent.parent / "data" / "source-health.json"
CHANGELOG_PATH = Path(__file__).resolve().parent.parent / "CHANGELOG-sources.md"

FAILURE_THRESHOLD = 5
WEB_SEARCH_TOOL = {"type": WEB_SEARCH_TYPE, "name": "web_search", "max_uses": 4}


def _load_health():
    if HEALTH_PATH.exists():
        return json.loads(HEALTH_PATH.read_text(encoding="utf-8"))
    return {}


def _save_health(health):
    HEALTH_PATH.write_text(json.dumps(health, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_changelog(source_name, old_url, new_url, note):
    CHANGELOG_PATH.touch(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n## {stamp} — {source_name}\n- Old URL: {old_url}\n- New URL: {new_url}\n- {note}\n"
    with CHANGELOG_PATH.open("a", encoding="utf-8") as f:
        f.write(entry)


def _validate_candidate(source, candidate):
    """Fetch the candidate URL/config exactly as the real pipeline would."""
    synthetic = dict(source)
    synthetic["url"] = candidate["url"]
    if candidate.get("kind"):
        synthetic["kind"] = candidate["kind"]
    if candidate.get("selector"):
        synthetic["selector"] = candidate["selector"]

    if synthetic["kind"] == "register":
        try:
            resp = _get(synthetic["url"])
            entities = _extract_entities(resp.text, synthetic.get("selector"), synthetic.get("column"))
            return len(entities) > 0
        except Exception:
            return False

    items, error, _ = fetch_source(synthetic, require_relevant=False)
    if error is not None or not items:
        return False
    # A no-selector "page" candidate scrapes every bare <a> on the page, so
    # almost ANY homepage would "validate" -- and a wrong heal is permanent
    # and invisible. Demand at least one topically relevant item in that mode:
    # a fair bar for a replacement of a digital-asset source.
    if synthetic["kind"] == "page" and not synthetic.get("selector"):
        return any(is_relevant(it["title"], it.get("summary", "")) for it in items)
    return True


def _find_replacement(source):
    prompt = (
        "A source feed used by a regulatory-news aggregator has gone dead (HTTP errors, "
        "unparseable, or returning zero items for several consecutive days). Find the "
        "current, correct, official URL that replaces it. Use web search.\n\n"
        f"Source name: {source['name']}\n"
        f"Jurisdiction: {source['jurisdiction']}\n"
        f"Previous URL: {source['url']}\n"
        f"Kind: {source['kind']} (feed = RSS/Atom, page = HTML listing to scrape, "
        "register = a page listing licensed entities)\n\n"
        "Reply with ONLY a JSON object, no markdown fences, no commentary:\n"
        '{"url": "https://...", "kind": "feed" or "page" or "register", '
        '"selector": "optional CSS selector for the repeating item/row, or null"}'
    )
    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=1500,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _extract_json_object(_extract_text(response))
        candidate = json.loads(raw)
        if not candidate.get("url") or not re.match(r"^https?://", candidate["url"], re.IGNORECASE):
            return None
        return candidate
    except Exception as exc:
        logger.warning("heal: replacement search failed for %s: %s", source["name"], exc)
        return None


def _dead_note(failures, last_error):
    """Honest, specific footer text for a source past the failure threshold,
    so a reader can tell a site that blocks automated fetch (needs a manual
    check) apart from one that has genuinely moved or gone empty."""
    err = (last_error or "").lower()
    runs = f"{failures} consecutive runs"
    if "403" in err or "429" in err or "forbidden" in err:
        return (f"Blocked by the site's bot protection for {runs} "
                "(content is unavailable to an automated fetch) — check the source manually.")
    if "no parseable items" in err or "zero" in err:
        return (f"Reachable but returned no parseable items for {runs} "
                "(the listing may be client-rendered or restructured) — check the source manually.")
    if last_error:
        return f"Failing for {runs} ({last_error}) — verify manually."
    return f"No response for {runs}; verify manually."


def health_check_and_heal(sources, fetch_results, register_health_notes):
    """Update failure counters, attempt self-heal for dead sources, and
    return the final source_health list for digest.json.

    `sources` is mutated in place when a source is healed.
    `fetch_results` is the dict returned by fetch.fetch_all: name -> {items, error, raw_count}.
    """
    health = _load_health()
    source_health = []
    register_notes_by_name = {n["name"]: n for n in register_health_notes}

    for source in sources:
        name = source["name"]
        state = health.get(name, {"consecutive_failures": 0})

        last_error = None
        if source.get("kind") == "register":
            note = register_notes_by_name.get(name)
            if note and note["status"] == "dead":
                state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
                last_error = note.get("note")
            else:
                state["consecutive_failures"] = 0
        else:
            result = fetch_results.get(name, {"items": [], "error": "not fetched", "raw_count": 0})
            # Use the pre-relevance-filter count: a general-mandate source
            # (OCC, ESMA, FATF...) routinely has zero *crypto* items on a
            # given day without being broken. Only an actual fetch/parse
            # failure, or a structurally broken extraction (raw_count == 0
            # even before topical filtering), counts as a failure.
            if result["error"] or result.get("raw_count", 0) == 0:
                state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
                last_error = result["error"] or "no parseable items"
            else:
                state["consecutive_failures"] = 0
        state["last_error"] = last_error

        if state["consecutive_failures"] < FAILURE_THRESHOLD:
            status = "ok"
            failures = state["consecutive_failures"]
            if failures:
                # Sub-threshold failure must not read as "responding normally"
                # -- the tab's own intro promises failures surface here.
                note_text = (f"Fetch failed this run ({last_error}); {failures} consecutive "
                             f"failure{'s' if failures > 1 else ''} — self-heal at {FAILURE_THRESHOLD}.")
            elif source.get("kind") == "register":
                reg_note = register_notes_by_name.get(name)
                note_text = reg_note["note"] if reg_note else "Register responding normally"
            else:
                note_text = "Feed responding normally"
        else:
            err = (last_error or "").lower()
            blocked = "403" in err or "429" in err or "forbidden" in err
            # A bot-blocked source has the CORRECT url -- it is simply
            # unfetchable from a datacenter IP. Never let heal swap in a
            # Claude-proposed alternative (which could be a wrong or unofficial
            # site that happens to fetch): that would corrupt a good source.
            # Same for {year}-templated urls: a healed candidate would be a
            # literal-year url that silently rots the following January --
            # the exact failure the template exists to prevent.
            no_heal = blocked or "{year}" in source.get("url", "")
            candidate = None if no_heal else _find_replacement(source)
            if candidate and _validate_candidate(source, candidate):
                old_url = source["url"]
                source["url"] = candidate["url"]
                if candidate.get("kind"):
                    source["kind"] = candidate["kind"]
                if candidate.get("selector"):
                    source["selector"] = candidate["selector"]
                _append_changelog(name, old_url, candidate["url"], "Auto-healed: old URL failed 5+ consecutive runs")
                state["consecutive_failures"] = 0
                status = "replaced"
                note_text = f"Auto-healed from {old_url}"
            else:
                status = "dead"
                note_text = _dead_note(state["consecutive_failures"], last_error)

        checked_at = datetime.now(timezone.utc).isoformat()
        state["last_checked"] = checked_at
        state["last_status"] = status
        health[name] = state
        source_health.append({
            "name": name, "status": status, "note": note_text,
            # Extra fields for the page's About tab / dated health rows;
            # render.py's validator passes unknown keys through untouched.
            "tier": source.get("tier", ""), "checked_at": checked_at,
        })

    _save_health(health)
    return source_health
