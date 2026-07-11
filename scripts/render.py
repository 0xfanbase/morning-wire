"""Render data/digest.json into the static docs/index.html page (plus the
docs/feed.xml RSS feed), using the designed template in
scripts/templates/page.html.
"""
import html as html_mod
import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "scripts" / "templates" / "page.html"
OUTPUT_PATH = ROOT / "docs" / "index.html"
FEED_PATH = ROOT / "docs" / "feed.xml"

SITE_URL = "https://0xfanbase.github.io/morning-wire/"
SITE_TITLE = "Digital Assets Morning Wire"
RADAR_MAX_ENTRIES = 8
# The Audit log is a public, unauthenticated page -- it should read as "the
# pipeline is alive and self-correcting", not as an incident postmortem.
# Entries are meant to be authored short (see the style rule in CLAUDE.md /
# audit/PLAYBOOK.md); this cap is a mechanical backstop, not the primary
# discipline. Only the most recent runs are kept, both to limit how much
# operational history a stranger can reconstruct and to keep the tab
# scannable.
RUN_LOG_MAX_ENTRIES = 10
RUN_LOG_NOTE_MAX_CHARS = 220

VALID_JURISDICTIONS = {"HK", "CN", "US", "UK", "EU", "SG", "GLOBAL"}
VALID_TYPES = {
    "enforcement", "final_rule", "consultation", "guidance", "designation",
    "licensing", "peer_move", "speech", "news",
}
VALID_PRIORITIES = {"high", "normal"}
VALID_STATUSES = {"new", "update"}
VALID_VERIFY_LEVELS = {"official", "corroborated", "single_source"}
VALID_HEALTH_STATUSES = {"ok", "replaced", "dead"}

# Plain-English labels for the RSS feed's <category> tags, mirroring
# JURIS_FULL / TYPE_LABEL in scripts/templates/page.html -- the feed's own
# docstring targets Outlook on locked-down bank desktops, the same reader the
# no-jargon standard protects everywhere else on the page; raw enum codes
# ("CN", "peer_move") are never shown to a reader anywhere but here.
JURIS_LABEL = {
    "HK": "Hong Kong", "CN": "Mainland China", "US": "United States",
    "UK": "United Kingdom", "EU": "European Union", "SG": "Singapore", "GLOBAL": "Global",
}
TYPE_LABEL = {
    "enforcement": "Enforcement", "final_rule": "Final rule", "consultation": "Consultation",
    "guidance": "Guidance", "designation": "Designation", "licensing": "Licensing",
    "peer_move": "Peer move", "speech": "Speech", "news": "News",
}


def _is_valid_iso8601(value):
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _normalize_iso(value):
    """Return a canonical, offset-carrying ISO string, or None if unparseable.

    Two failure modes this prevents on the public page:
    - Python's fromisoformat is far more lenient than JS Date ("20260707",
      "2026-07-07T08", week dates) -- an accepted-but-JS-unparseable string
      makes Intl.DateTimeFormat throw and blanks the whole digest.
    - An offset-LESS date-time is read by JS in the VIEWER'S local timezone,
      so the same card would show different dates to readers in different
      countries on a page that promises Hong Kong time everywhere.
    Naive values are assumed UTC (the pipeline's internal convention).
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _is_http_url(value):
    # URL fields skip _clean_text (a URL can't have its illegal characters
    # silently stripped the way free text can -- that would just corrupt the
    # link), so this gate must reject them itself: an unscrubbed control
    # character or lone surrogate here used to reach feed.xml unescaped
    # (breaking XML parsing for every subscriber) or crash write_text()'s
    # UTF-8 encoding outright, taking down the whole day's render for one bad
    # scraped item -- see audit/lessons.md L5.
    return (isinstance(value, str)
            and re.match(r"^https?://", value, re.IGNORECASE) is not None
            and not _ILLEGAL_TEXT_CHARS_RE.search(value))


def _truncate_gracefully(text, limit):
    """Cut at the limit, then back up to the last word boundary and add an
    ellipsis -- a flat [:limit] slice can (and did) sever a sentence mid-word
    with no visual indication anything was cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(".,;: ")
    return cut + "…" if cut else text[:limit]


# C0 controls other than tab/newline/CR, plus DEL, plus any surrogate code
# point, are illegal in XML 1.0 (they break docs/feed.xml outright for every
# reader) and a lone/unpaired surrogate additionally crashes UTF-8 encoding
# (str.encode raises), which would crash the ENTIRE render, not just the
# feed. json.loads happily accepts an unpaired \uD800-\uDFFF escape from a
# scraped title into a plain Python str, so this must be scrubbed here,
# before any such string reaches either output -- see audit/lessons.md L5.
_ILLEGAL_TEXT_CHARS_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]")


def _clean_text(value):
    """Strip characters that are illegal in XML 1.0 or that crash UTF-8
    encoding outright, from a free-text field. Non-strings pass through
    unchanged (callers that need a string coerce first)."""
    if not isinstance(value, str):
        return value
    return _ILLEGAL_TEXT_CHARS_RE.sub("", value)


def _valid_item(item):
    """Defensive schema gate: one malformed item must never break render for
    everyone else (a single bad date/enum used to crash the client-side
    Intl.DateTimeFormat call for the whole page).
    """
    if not isinstance(item, dict):
        return False
    required = ("id", "jurisdiction", "source", "title", "url", "published", "type",
                "priority", "status", "verification", "summary", "so_what", "first_seen")
    if not all(k in item for k in required):
        return False
    # Identity/text fields must be real non-empty strings: a null title would
    # otherwise publish the literal word "null" on the public page.
    for key in ("id", "title", "url", "source"):
        if not isinstance(item[key], str) or not item[key].strip():
            return False
    for key in ("title", "source"):
        item[key] = _clean_text(item[key])
    if not item["title"].strip() or not item["source"].strip():
        return False  # cleaning removed everything -- e.g. a title of only control chars
    # Normalize dates to canonical offset-carrying ISO (drop if unparseable).
    for key in ("published", "first_seen"):
        normalized = _normalize_iso(item[key])
        if normalized is None:
            return False
        item[key] = normalized
    if item["type"] not in VALID_TYPES:
        return False
    if item["priority"] not in VALID_PRIORITIES:
        return False
    if item["status"] not in VALID_STATUSES:
        return False
    if not _is_http_url(item["url"]):
        return False
    verification = item.get("verification") or {}
    # Defensive: a hand-edited enrichment session can produce a malformed
    # verification shape (a bare string, a list, sources as a dict, or a
    # source that isn't an object). This gate must DROP such an item, never
    # raise -- one bad item must not crash the render for the whole page.
    if not isinstance(verification, dict):
        return False
    if verification.get("level") not in VALID_VERIFY_LEVELS:
        return False
    sources = verification.get("sources", [])
    if not isinstance(sources, list):
        return False
    for src in sources:
        if (not isinstance(src, dict) or not _is_http_url(src.get("url", ""))
                or not isinstance(src.get("name"), str) or not src["name"].strip()):
            return False
        src["name"] = _clean_text(src["name"])
    if verification["level"] == "corroborated" and len(sources) < 2:
        # The badge text asserts "N sources" and the client dereferences the
        # list -- a corroborated claim without its evidence is invalid.
        return False
    # Optional fact-check record written by the enrichment session. Honesty
    # is structural: a record missing its timestamp or the named authority it
    # was checked against is stripped, never displayed half-formed.
    checked = verification.get("checked")
    if checked is not None:
        checked_at = _normalize_iso(checked.get("at")) if isinstance(checked, dict) else None
        if (not isinstance(checked, dict) or not checked_at
                or not isinstance(checked.get("against"), str) or not checked["against"].strip()
                or (checked.get("url") is not None and not _is_http_url(checked["url"]))):
            verification.pop("checked", None)
        else:
            checked["at"] = checked_at
            checked["against"] = _clean_text(checked["against"])
            checked["note"] = _truncate_gracefully(_clean_text(str(checked.get("note") or "")), 300)
    # Optional date provenance: whitelist or drop.
    if item.get("date_source") not in ("feed", "page", "fetch_time", "verified"):
        item.pop("date_source", None)
    # Degrade gracefully on missing/non-string prose rather than dropping:
    # keyless-mode convention is summary == title.
    if not isinstance(item["summary"], str) or not item["summary"].strip():
        item["summary"] = item["title"]
    else:
        item["summary"] = _clean_text(item["summary"]) or item["title"]
    if not isinstance(item["so_what"], str) or not item["so_what"].strip():
        item["so_what"] = "Review the source directly; automated analysis unavailable."
    else:
        item["so_what"] = (_clean_text(item["so_what"]) or
                            "Review the source directly; automated analysis unavailable.")
    if item["jurisdiction"] not in VALID_JURISDICTIONS:
        print(f"[render] item {item['id']!r} has unknown jurisdiction {item['jurisdiction']!r} -- folded to GLOBAL")
        item["jurisdiction"] = "GLOBAL"  # unknown jurisdiction -> fold into Global rather than drop
    return True


def _valid_radar_entry(entry, generated_at):
    """Radar rows are forward-looking deadlines/effective dates maintained by
    the enrichment session: {date, label, jurisdiction, url?}. Past-dated rows
    are dropped automatically so the strip self-prunes as deadlines pass --
    that pruning is intentional and NOT logged; only genuine malformation is."""
    if not isinstance(entry, dict):
        print(f"[render] dropped a malformed radar entry: {entry!r}")
        return False
    date = entry.get("date")
    if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        print(f"[render] dropped a radar entry with a bad date: {entry.get('date')!r} ({entry.get('label')!r})")
        return False
    if not isinstance(entry.get("label"), str) or not entry["label"].strip():
        print(f"[render] dropped a radar entry with no label: {entry!r}")
        return False
    entry["label"] = _clean_text(entry["label"])
    if not entry["label"].strip():
        print(f"[render] dropped a radar entry whose label was only illegal characters: {entry!r}")
        return False
    if entry.get("jurisdiction") not in VALID_JURISDICTIONS:
        entry["jurisdiction"] = "GLOBAL"
    if entry.get("url") is not None and not _is_http_url(entry["url"]):
        print(f"[render] dropped radar entry {entry['label']!r} with a bad url: {entry.get('url')!r}")
        return False
    # Keep rows dated today (HKT) or later.
    gen_day_hkt = (datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                   .astimezone(timezone(timedelta(hours=8))).date().isoformat())
    return date >= gen_day_hkt


def _source_jurisdiction_map():
    """name -> jurisdiction, cross-referenced from data/sources.json.

    source_health rows are populated by the pipeline's health-check step and
    carry no jurisdiction field of their own; the client needs one to detect
    a jurisdiction whose official coverage has gone dark (see audit finding
    E3). Best-effort: an unreadable/missing sources.json just yields no
    jurisdiction annotations rather than failing the render.
    """
    path = ROOT / "data" / "sources.json"
    try:
        sources = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {s["name"]: s.get("jurisdiction") for s in sources
            if isinstance(s, dict) and isinstance(s.get("name"), str)}


def _valid_health_entry(entry, source_jurisdictions):
    if not isinstance(entry, dict):
        print(f"[render] dropped a malformed source_health entry: {entry!r}")
        return False
    if entry.get("status") not in VALID_HEALTH_STATUSES:
        # Degrade rather than drop -- matching how name/note already degrade
        # below -- so a row with a merely-invalid status still shows up in
        # the very tab whose intro promises "a source is never silently
        # dropped", instead of vanishing from it. "dead" is the conservative
        # default: an unrecognised status is itself a signal something needs
        # attention, not evidence the source is actually healthy.
        print(f"[render] source_health entry {entry.get('name')!r} has invalid status "
              f"{entry.get('status')!r} -- degraded to 'dead'")
        entry["status"] = "dead"
    entry["note"] = _clean_text(str(entry.get("note") or ""))
    entry["name"] = _clean_text(str(entry.get("name") or "")) or "unknown source"
    jurisdiction = source_jurisdictions.get(entry["name"])
    if jurisdiction in VALID_JURISDICTIONS:
        entry["jurisdiction"] = jurisdiction
    return True


def sanitize_digest(digest):
    """Validate + repair a digest.json payload before it is ever embedded in
    the public page. Drops individually malformed items/health rows instead
    of failing the whole render.
    """
    # hkDayKey() throws client-side on an unparseable date, blanking the
    # whole page -- never embed a generated_at we haven't normalized.
    generated_at = _normalize_iso(digest.get("generated_at")) or datetime.now(timezone.utc).isoformat()
    # `or []` on every list: a hand-edited digest with "items": null must
    # degrade to an empty page, not crash the render.
    items_in = digest.get("items") or []
    source_jurisdictions = _source_jurisdiction_map()
    clean_run_log = []
    for e in (digest.get("run_log") or []):
        at = _normalize_iso(e.get("at")) if isinstance(e, dict) else None
        if not at:
            print(f"[render] dropped a malformed run_log entry: {e!r}")
            continue
        clean_run_log.append({
            "at": at,
            "note": _truncate_gracefully(_clean_text(str(e.get("note") or "")), RUN_LOG_NOTE_MAX_CHARS),
        })
    clean_items = [it for it in items_in if _valid_item(it)]
    item_drop_count = len(items_in) - len(clean_items)
    clean = {
        "generated_at": generated_at,
        "top_of_mind": _truncate_gracefully(_clean_text(str(digest.get("top_of_mind") or "")), 400),
        "items": clean_items,
        # Reader-facing count of items dropped by the schema gate THIS run --
        # not cumulative, not a history. page.html shows a same-day notice
        # when this is nonzero, and the empty state stops claiming a
        # confirmed-quiet day when it isn't one -- a schema regression that
        # fails every item must never render identically to a genuinely
        # quiet day (see audit finding A3).
        "item_drop_count": item_drop_count,
        "source_health": [h for h in (digest.get("source_health") or [])
                           if _valid_health_entry(h, source_jurisdictions)],
        "run_log": clean_run_log[-RUN_LOG_MAX_ENTRIES:],
        "radar": sorted(
            [e for e in (digest.get("radar") or []) if _valid_radar_entry(e, generated_at)],
            key=lambda e: e["date"],
        )[:RADAR_MAX_ENTRIES],
    }
    if item_drop_count:
        print(f"[render] dropped {item_drop_count} malformed item(s) before publishing")
    return clean


def _safe_json_embed(digest):
    """JSON-encode for embedding inside an inline <script> block.

    json.dumps does not escape '<', so a title/summary containing
    "</script>" (or "<!--") could break out of the script tag and inject
    arbitrary HTML into a public, unauthenticated page. Escaping every '<'
    (plus the JS line-terminator characters U+2028/U+2029, which used to be
    illegal inside JS string literals) makes the embed inert regardless of
    what a scraped title contains.
    """
    raw = json.dumps(digest, ensure_ascii=False)
    raw = raw.replace("<", "\\u003c")
    raw = raw.replace(" ", "\\u2028").replace(" ", "\\u2029")
    return raw


def _og_strings(clean):
    """Day-fresh Open Graph title/description so a pasted link unfurls in
    Teams/WhatsApp with today's synthesis instead of a bare URL."""
    gen = datetime.fromisoformat(clean["generated_at"].replace("Z", "+00:00"))
    hkt_day = (gen.astimezone(timezone(timedelta(hours=8)))).strftime("%Y-%m-%d")
    title = f"{SITE_TITLE} — {hkt_day}"
    desc = clean["top_of_mind"].strip()
    if not desc:
        n = len(clean["items"])
        desc = (f"{n} item{'s' if n != 1 else ''} across Hong Kong, mainland China, the United "
                "States, the United Kingdom, the European Union, Singapore and global "
                "standard-setters. AI-sourced and AI-generated — verify against official sources.")
    return _truncate_gracefully(title, 200), _truncate_gracefully(desc, 200)


def render_feed(clean):
    """Write docs/feed.xml (RSS 2.0). One extra static output turns a page you
    must remember to visit into a channel Outlook's built-in RSS folder — a
    guaranteed fixture on locked-down bank desktops — can pull automatically.
    guid is the stable item id (isPermaLink=false) so readers dedupe correctly
    across the rolling 7-day window."""
    def rfc822(iso):
        return format_datetime(datetime.fromisoformat(iso.replace("Z", "+00:00")))

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{xml_escape(SITE_TITLE)}</title>",
        f"<link>{xml_escape(SITE_URL)}</link>",
        "<description>Daily digital-asset regulatory and market digest. "
        "AI-sourced and AI-generated — verify against official sources before acting.</description>",
        f"<lastBuildDate>{rfc822(clean['generated_at'])}</lastBuildDate>",
        f'<atom:link href="{xml_escape(SITE_URL)}feed.xml" rel="self" type="application/rss+xml"/>',
    ]
    for it in clean["items"]:
        desc = f"{it['summary']} So what: {it['so_what']} [{it['verification']['level'].replace('_', ' ')}]"
        parts += [
            "<item>",
            f"<title>{xml_escape(it['title'])}</title>",
            f"<link>{xml_escape(it['url'])}</link>",
            f'<guid isPermaLink="false">{xml_escape(it["id"])}</guid>',
            f"<pubDate>{rfc822(it['published'])}</pubDate>",
            f"<description>{xml_escape(desc)}</description>",
            f"<category>{xml_escape(JURIS_LABEL.get(it['jurisdiction'], it['jurisdiction']))}</category>",
            f"<category>{xml_escape(TYPE_LABEL.get(it['type'], it['type']))}</category>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>", ""]
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    FEED_PATH.write_text("\n".join(parts), encoding="utf-8")


def render(digest):
    """Render a digest dict to docs/index.html (+ docs/feed.xml). Returns the
    rendered HTML string (also written to disk) so callers can validate
    before committing.
    """
    clean = sanitize_digest(digest)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    for placeholder in ("__DIGEST_JSON__", "__OG_TITLE__", "__OG_DESC__"):
        if placeholder not in template:
            raise RuntimeError(f"template is missing the {placeholder} placeholder")

    og_title, og_desc = _og_strings(clean)
    html = (template
            .replace("__DIGEST_JSON__", _safe_json_embed(clean))
            .replace("__OG_TITLE__", html_mod.escape(og_title, quote=True))
            .replace("__OG_DESC__", html_mod.escape(og_desc, quote=True)))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    render_feed(clean)
    return html


if __name__ == "__main__":
    digest_path = ROOT / "data" / "digest.json"
    render(json.loads(digest_path.read_text(encoding="utf-8")))
    print(f"[render] wrote {OUTPUT_PATH}")
