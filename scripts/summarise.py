"""Claude API calls: per-item summarisation, priority/type classification,
and materiality judging for re-surfaced (same-URL, changed-title) items.
"""
import json
import logging
import os
import re

import anthropic

logger = logging.getLogger(__name__)

# Overridable without a code change (repo Settings -> Variables/Secrets ->
# pass through digest.yml env) so a model retirement or web-search tool
# version bump never requires editing scripts.
MODEL = os.environ.get("REG_RADAR_MODEL", "claude-sonnet-4-6")
WEB_SEARCH_TYPE = os.environ.get("REG_RADAR_WEB_SEARCH_TYPE", "web_search_20260209")
MAX_ITEMS_PER_RUN = 25

VALID_TYPES = {
    "enforcement", "final_rule", "consultation", "guidance", "designation",
    "licensing", "peer_move", "speech", "news",
}

_client = None


def get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _strip_fences(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_text(response):
    return "".join(b.text for b in response.content if b.type == "text")


SYSTEM_PROMPT = """You write for Reg Radar, a neutral daily digital-asset regulatory \
digest read by financial-crime-compliance (FCC) professionals at banks across Hong Kong, \
mainland China, the US, EU, Singapore and globally. The primary reader is HK-based and \
covers digital assets at an international bank, but the published output must read as \
neutral wire-service copy usable by any compliance team -- never write in first person \
plural ("we", "our bank"), never name or imply a specific employer, never address "your \
firm". Write plainly: no insider jargon, no hype, no speculation beyond the source.

Spell out institution and instrument names in full -- "the Hong Kong Monetary \
Authority", not "the HKMA"; "virtual-asset", not "VA". Avoid unexplained acronyms: \
expand on first use, adding the acronym in parentheses only when it is the commonly \
used name (e.g. "the Financial Action Task Force (FATF)").

For each item, return:
- summary: one plain-English sentence describing what happened, readable by any \
compliance team member with no specialist crypto background.
- so_what: one practical sentence of implication for an HK/China-focused digital-asset \
FCC function at an international bank -- concrete and neutral, never naming any specific \
firm as "the reader's employer".
- type: exactly one of enforcement, final_rule, consultation, guidance, designation, \
licensing, peer_move, speech, news.
- priority: "high" or "normal". Use "high" for enforcement actions, final rules, \
sanctions/designations, licensing grants, and anything material touching Hong Kong or \
mainland China, or touching stablecoins, custody, tokenisation/RWA, prudential treatment \
of bank cryptoasset exposures, sanctions/travel-rule, AML/CFT rulemaking. Otherwise \
"normal"."""


def _build_batch_prompt(items):
    payload = [
        {
            "idx": i,
            "source": it.get("source", ""),
            "jurisdiction": it.get("jurisdiction", ""),
            "title": it.get("title", ""),
            "context": (it.get("summary") or "")[:500],
        }
        for i, it in enumerate(items)
    ]
    return (
        "Classify and summarise each of these regulatory/market items for the digest. "
        "Return ONLY a JSON object shaped like:\n"
        '{"top_of_mind": "...", "items": [{"idx": <int>, "summary": "...", "so_what": "...", '
        '"type": "...", "priority": "high"|"normal"}]}\n\n'
        "items: exactly one object per input item, in the same order.\n"
        "top_of_mind: one or two sentences (max ~45 words) saying what is top of mind "
        "today for the compliance reader, synthesising the day's highest-priority items; "
        "plain English, no acronyms, neutral; empty string if nothing stands out.\n"
        "No markdown fences, no commentary, no extra keys.\n\nITEMS:\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _fallback_result(idx):
    return {"idx": idx, "summary": "", "so_what": "", "type": "news", "priority": "normal"}


def select_top(items, cap=MAX_ITEMS_PER_RUN):
    """Prioritise official-tier and most-recent items, capping the list that
    verify.py and summarise_items() both operate on -- this must run once,
    before either step, so every item that gets a verification badge also
    gets a summary (and vice versa).
    """
    ordered = sorted(items, key=lambda it: it.get("published", ""), reverse=True)
    ordered = sorted(ordered, key=lambda it: it.get("tier") != "official")
    selected = ordered[:cap]
    dropped = len(ordered) - len(selected)
    if dropped > 0:
        logger.warning("dropping %d item(s) beyond the %d/run cap", dropped, cap)
    return selected


def summarise_items(items):
    """Classify items via one batched Claude call and merge results back onto
    each item dict in place. Callers should run select_top() first so this
    and verify_items() operate on the same, already-capped item list.

    Returns (ok, top_of_mind). ok is False when the batch call fell back to
    per-item defaults -- callers should surface that to the reader (e.g. via
    source_health) rather than let degraded AI enrichment pass silently.
    top_of_mind is a 1-2 sentence synthesis of the day's highest-priority
    items ("" when nothing stands out or the call failed).
    """
    if not items:
        return True, ""

    selected = items  # run.py is expected to have already called select_top()
    client = get_client()
    ok = True
    top_of_mind = ""
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_batch_prompt(selected)}],
        )
        raw = _strip_fences(_extract_text(response))
        parsed = json.loads(raw)
        if isinstance(parsed, list):  # tolerate the bare-array shape
            results = parsed
        else:
            results = parsed.get("items", [])
            top_of_mind = str(parsed.get("top_of_mind") or "").strip()
        by_idx = {r.get("idx"): r for r in results if isinstance(r, dict)}
    except Exception as exc:
        logger.error("summarise: batch call failed, using fallbacks: %s", exc)
        by_idx = {}
        ok = False

    for i, item in enumerate(selected):
        result = by_idx.get(i, _fallback_result(i))
        item["summary"] = result.get("summary") or item.get("title", "")
        item["so_what"] = result.get("so_what") or "Review the source directly; automated analysis unavailable."
        item_type = result.get("type")
        item["type"] = item_type if item_type in VALID_TYPES else "news"
        item["priority"] = "high" if result.get("priority") == "high" else "normal"

    return ok, top_of_mind


MATERIAL_KEYWORDS = [
    "final", "finalis", "finaliz", "adopt", "penalty", "fine", "fined", "settle",
    "sanction", "revoke", "suspend", "grant", "effective", "enforc", "designat",
    "licence", "license", "ban ", "prohibit",
]

MAX_MATERIALITY_CALLS_PER_RUN = 5


def looks_material(old_title, new_title):
    haystack = f"{old_title} {new_title}".lower()
    return any(kw in haystack for kw in MATERIAL_KEYWORDS)


def judge_material_update(old_title, new_item, calls_used):
    """Ask Claude whether a same-URL, changed-title item is a material update
    worth resurfacing (vs. a cosmetic edit). Capped by the caller via calls_used.
    Returns True/False; defaults to False (skip) on any failure.
    """
    if calls_used >= MAX_MATERIALITY_CALLS_PER_RUN:
        return False
    client = get_client()
    prompt = (
        "A regulatory/news source page we've already seen has changed its title.\n"
        f"Previous title: {old_title!r}\n"
        f"New title: {new_item.get('title', '')!r}\n"
        f"Source: {new_item.get('source', '')}\n\n"
        "Is this a MATERIAL development worth re-surfacing to compliance readers -- e.g. "
        "an enforcement outcome now decided, a final rule now adopted, a penalty amount "
        "now set, a licence now granted, an effective date now set? Or is it a cosmetic "
        "edit (typo fix, formatting, minor rewording) not worth re-surfacing?\n\n"
        'Reply with ONLY a JSON object: {"material": true or false}'
    )
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = _strip_fences(_extract_text(response))
        return bool(json.loads(raw).get("material", False))
    except Exception as exc:
        logger.warning("judge_material_update failed, defaulting to skip: %s", exc)
        return False
