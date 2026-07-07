"""Render data/digest.json into the static docs/index.html page, using the
designed template in scripts/templates/page.html unmodified in layout.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "scripts" / "templates" / "page.html"
OUTPUT_PATH = ROOT / "docs" / "index.html"

VALID_JURISDICTIONS = {"HK", "CN", "US", "EU", "SG", "GLOBAL"}
VALID_TYPES = {
    "enforcement", "final_rule", "consultation", "guidance", "designation",
    "licensing", "peer_move", "speech", "news",
}
VALID_PRIORITIES = {"high", "normal"}
VALID_STATUSES = {"new", "update"}
VALID_VERIFY_LEVELS = {"official", "corroborated", "single_source"}
VALID_HEALTH_STATUSES = {"ok", "replaced", "dead"}


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
    return isinstance(value, str) and re.match(r"^https?://", value, re.IGNORECASE) is not None


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
        if not isinstance(src, dict) or not _is_http_url(src.get("url", "")):
            return False
    if verification["level"] == "corroborated" and len(sources) < 2:
        # The badge text asserts "N sources" and the client dereferences the
        # list -- a corroborated claim without its evidence is invalid.
        return False
    # Degrade gracefully on missing/non-string prose rather than dropping:
    # keyless-mode convention is summary == title.
    if not isinstance(item["summary"], str) or not item["summary"].strip():
        item["summary"] = item["title"]
    if not isinstance(item["so_what"], str) or not item["so_what"].strip():
        item["so_what"] = "Review the source directly; automated analysis unavailable."
    if item["jurisdiction"] not in VALID_JURISDICTIONS:
        item["jurisdiction"] = "GLOBAL"  # unknown jurisdiction -> fold into Global rather than drop
    return True


def _valid_health_entry(entry):
    if not isinstance(entry, dict):
        return False
    if entry.get("status") not in VALID_HEALTH_STATUSES:
        return False
    entry["note"] = str(entry.get("note") or "")
    entry["name"] = str(entry.get("name") or "unknown source")
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
    clean = {
        "generated_at": generated_at,
        "top_of_mind": str(digest.get("top_of_mind") or "")[:400],
        "items": [it for it in items_in if _valid_item(it)],
        "source_health": [h for h in (digest.get("source_health") or []) if _valid_health_entry(h)],
        "run_log": [
            {"at": _normalize_iso(e.get("at")), "note": str(e.get("note") or "")[:300]}
            for e in (digest.get("run_log") or [])
            if isinstance(e, dict) and _normalize_iso(e.get("at"))
        ][-30:],
    }
    dropped = len(items_in) - len(clean["items"])
    if dropped:
        print(f"[render] dropped {dropped} malformed item(s) before publishing")
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


def render(digest):
    """Render a digest dict to docs/index.html. Returns the rendered HTML
    string (also written to disk) so callers can validate before committing.
    """
    clean = sanitize_digest(digest)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")

    if "__DIGEST_JSON__" not in template:
        raise RuntimeError("template is missing the __DIGEST_JSON__ placeholder")

    html = template.replace("__DIGEST_JSON__", _safe_json_embed(clean))

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    return html


if __name__ == "__main__":
    digest_path = ROOT / "data" / "digest.json"
    render(json.loads(digest_path.read_text(encoding="utf-8")))
    print(f"[render] wrote {OUTPUT_PATH}")
