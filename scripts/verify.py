"""Corroboration of industry-sourced items via Claude + web_search.

tier=official items are trusted as-is (the regulator IS the source).
tier=industry items must be confirmed against an official source or a
second independent reputable outlet before they earn a "corroborated" badge;
otherwise they are flagged "single_source" so readers know to verify.
"""
import json
import logging
import re

from summarise import get_client, MODEL, WEB_SEARCH_TYPE, _strip_fences, _extract_text  # noqa: F401 (reuse)

logger = logging.getLogger(__name__)

MAX_VERIFY_CALLS_PER_RUN = 10

WEB_SEARCH_TOOL = {"type": WEB_SEARCH_TYPE, "name": "web_search", "max_uses": 3}


def _base_source(item):
    return {"name": item.get("source", ""), "url": item.get("url", "")}


def _looks_like_url(url):
    return bool(re.match(r"^https?://", url or "", re.IGNORECASE))


def verify_item(item, calls_used):
    """Return (verification_dict, calls_made)."""
    base = _base_source(item)

    if item.get("tier") == "official":
        return {"level": "official", "sources": [base]}, 0

    if calls_used >= MAX_VERIFY_CALLS_PER_RUN:
        return {"level": "single_source", "sources": [base]}, 0

    prompt = (
        "This is an industry-media report, not an official regulator statement. "
        "Use web search to check whether it is confirmed by EITHER an official "
        "regulator/government source OR a second independent reputable outlet "
        "(e.g. Reuters, Bloomberg, Financial Times, or an equivalent established "
        "wire/financial-press outlet) that is NOT the original outlet below.\n\n"
        f"Original outlet: {item.get('source', '')}\n"
        f"Title: {item.get('title', '')}\n"
        f"Context: {(item.get('summary') or '')[:500]}\n\n"
        "Reply with ONLY a JSON object, no markdown fences, no commentary:\n"
        '{"confirmed": true or false, "source": {"name": "...", "url": "https://..."} or null}'
    )

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=1500,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _strip_fences(_extract_text(response))
        result = json.loads(raw)
    except Exception as exc:
        logger.warning("verify_item failed for %s: %s", item.get("url"), exc)
        return {"level": "single_source", "sources": [base]}, 1

    confirming = result.get("source") if result.get("confirmed") else None
    if (
        isinstance(confirming, dict)
        and confirming.get("name")
        and _looks_like_url(confirming.get("url"))
        and confirming.get("name", "").strip().lower() != base["name"].strip().lower()
    ):
        return {"level": "corroborated", "sources": [base, {"name": confirming["name"], "url": confirming["url"]}]}, 1

    return {"level": "single_source", "sources": [base]}, 1


def verify_items(items):
    """Attach verification to every item in place. Returns the same list."""
    calls_used = 0
    for item in items:
        verification, calls_made = verify_item(item, calls_used)
        calls_used += calls_made
        item["verification"] = verification
    return items
