"""SOFT. Flags a `top_of_mind` callout whose claims don't trace back to the
Today pool it renders beside.

The page shows `top_of_mind` ONLY on the exact, unfiltered Today view
(page.html's inTodayAllAllView), where "Today" is the set of items whose
`first_seen` falls on the same Hong Kong calendar day as `generated_at`. The
enrichment recipe (CLAUDE.md step 4) requires every claim in the callout to
trace back to an item's own summary/so_what "already in this digest" -- but
a session working through a multi-day backlog can synthesise the callout
from OLD items, producing a confident "top of mind today" about stories that
are not in the view it sits next to (found live on 2026-07-24: both callout
claims traced to items first seen days earlier).

Heuristic and its limits, stated honestly: a sentence "maps" to a Today-pool
item when it shares at least BIGRAM_THRESHOLD adjacent word pairs with that
item's title+summary+so_what, or -- as a fallback for reworded-but-grounded
prose -- at least UNIGRAM_THRESHOLD distinct contentful words. Word pairs
are the primary signal because single words are hopeless on this page: every
other item legitimately contains "united states", "money laundering",
"financial", "enforcement" (validated against live data -- a sentence about
a FinCEN comment period reached 5 shared single words with an unrelated
Chile money-laundering story). Lexical overlap of any order is still a
proxy: it cannot see paraphrase (a genuinely-grounded sentence rewritten in
fully novel words would false-positive) and could in principle be gamed by
boilerplate-heavy prose (a stale sentence false-negativing past the
threshold). That is why this check is SOFT and warn-level -- a nudge for the
next enrichment session's judgment, never a gate and never auto-applied.
Report-only: only the model that actually read the day's items should
rewrite the callout. In practice the CLAUDE.md house style helps the
heuristic: both the callout and item prose must spell names out in full, so
genuinely-derived sentences share exact multi-word names with their source
item.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from base import finding, could_not_run

CHECK_ID = "top_of_mind_freshness"
MODE = "soft"

BIGRAM_THRESHOLD = 3
UNIGRAM_THRESHOLD = 6
HKT = timezone(timedelta(hours=8))

# Function words plus domain vocabulary so generic it appears in nearly every
# item on this page -- shared "crypto"/"digital assets" boilerplate must not
# be able to vouch for a sentence on its own.
STOPWORDS = {
    "the", "and", "for", "not", "but", "its", "are", "was", "were", "had",
    "that", "this", "with", "from", "have", "has", "been", "being", "will",
    "would", "should", "could", "must", "into", "over", "under", "after",
    "before", "their", "them", "they", "these", "those", "there", "where",
    "which", "while", "than", "then", "when", "what", "whose", "also", "more",
    "most", "some", "such", "very", "just", "only", "said", "says", "means",
    "meaning", "separately", "about", "against", "between", "during",
    "through", "toward", "towards",
    "crypto", "cryptoasset", "cryptoassets", "cryptocurrency", "cryptocurrencies",
    "digital", "asset", "assets", "virtual", "token", "tokens",
}


def _words(text):
    return [w for w in re.findall(r"[a-z0-9]{3,}", str(text).lower()) if w not in STOPWORDS]


def _tokens(text):
    return {w for w in _words(text) if len(w) >= 4}


def _bigrams(text):
    words = _words(text)
    return set(zip(words, words[1:]))


def _sentences(text):
    # Good enough for the short, plain-English prose CLAUDE.md mandates here
    # (max ~45 words, no abbreviations-with-periods style).
    parts = re.split(r"(?<=[.!?])\s+", str(text).strip())
    return [p.strip() for p in parts if p.strip()]


def run(repo_root):
    try:
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/digest.json: {exc}")]

    top_of_mind = str(digest.get("top_of_mind") or "").strip()
    if not top_of_mind:
        return []  # empty is always legitimate (quiet days) -- nothing to trace

    try:
        gen = datetime.fromisoformat(str(digest.get("generated_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return [could_not_run(CHECK_ID, "generated_at is missing/unparseable -- cannot establish the Today pool")]
    gen_day = gen.astimezone(HKT).date().isoformat()

    pool = []
    for it in digest.get("items", []):
        try:
            fs = datetime.fromisoformat(str(it.get("first_seen", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if fs.astimezone(HKT).date().isoformat() == gen_day:
            pool.append(it)

    if not pool:
        return [finding(
            CHECK_ID, "warn",
            "top_of_mind is non-empty but the Today pool has no items at all",
            f"generated_at falls on HKT day {gen_day} and no item's first_seen shares that day, yet "
            f"top_of_mind reads: {top_of_mind[:160]!r}. The callout renders beside an empty Today "
            "view claiming something is top of mind today. The next enrichment session should "
            "rewrite it from the actual pool or set it to \"\" (CLAUDE.md step 4's quiet-day rule).",
            {"generated_hkt_day": gen_day, "top_of_mind": top_of_mind[:200]},
        )]

    item_texts = [(it.get("id"), f"{it.get('title', '')} {it.get('summary', '')} {it.get('so_what', '')}")
                  for it in pool]
    item_scored = [(item_id, _bigrams(text), _tokens(text)) for item_id, text in item_texts]

    findings = []
    for sentence in _sentences(top_of_mind):
        sent_bigrams, sent_tokens = _bigrams(sentence), _tokens(sentence)
        if not sent_tokens:
            continue
        best = {"id": None, "bigrams": 0, "unigrams": 0}
        mapped = False
        for item_id, bigs, toks in item_scored:
            b, u = len(sent_bigrams & bigs), len(sent_tokens & toks)
            if b >= BIGRAM_THRESHOLD or u >= UNIGRAM_THRESHOLD:
                mapped = True
                break
            if (b, u) > (best["bigrams"], best["unigrams"]):
                best = {"id": item_id, "bigrams": b, "unigrams": u}
        if not mapped:
            findings.append(finding(
                CHECK_ID, "warn",
                "a top_of_mind sentence doesn't trace to any item in the Today pool",
                f"Sentence {sentence[:140]!r} shares at most {best['bigrams']} word pair(s) / "
                f"{best['unigrams']} contentful word(s) with any of the {len(pool)} item(s) first "
                f"seen on HKT day {gen_day} (closest: {best['id']}). CLAUDE.md step 4 requires every "
                "claim to trace back to an item already in the view the callout renders beside -- "
                "likely a stale synthesis from an earlier backlog. Judgment call for the next "
                "enrichment session; lexical overlap is a proxy, so verify before rewriting.",
                {"sentence": sentence[:200], "generated_hkt_day": gen_day,
                 "best_bigram_overlap": best["bigrams"], "best_unigram_overlap": best["unigrams"],
                 "closest_item": best["id"]},
            ))
    return findings
