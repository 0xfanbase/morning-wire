"""Feed/page fetching + parsing for Reg Radar sources."""
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# A plain browser UA -- several regulator sites (SFC, The Block) 403 the
# default python-requests / generic bot UA but serve the same public press
# releases fine to an ordinary browser string.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
TIMEOUT_SECS = 15
MAX_RETRIES = 2

DATE_FORMATS = [
    "%Y-%m-%d",
    "%d %B %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%Y/%m/%d",
]

# Simple topical gate so general-mandate regulator feeds (bank supervision,
# futures, securities-at-large) don't flood the digest with non-digital-asset
# items. Crypto-native outlets (CoinDesk/The Block) pass this trivially.
RELEVANCE_KEYWORDS = [
    "crypto", "digital asset", "digital-asset", "virtual asset", "stablecoin",
    "stable coin", "tokeni", "blockchain", "distributed ledger", "dlt",
    "vasp", "casp", "vatp", "dpt", "web3", "web 3", "bitcoin", "btc",
    "ethereum", "nft", "e-cny", "ecny", "cbdc", "mica", "travel rule",
    "virtual currency", "defi", "decentralized finance", "decentralised finance",
    "crypto mixer", "crypto-asset", "cryptoasset",
    "e-hkd", "digital yuan", "digital renminbi", "digital currency",
    "project ensemble", "mbridge", "m-bridge", "wallet", "self-custody",
]


def resolve_url(url):
    """Substitute {year} with the current Hong Kong-time year -- for sources
    whose listing URL is year-partitioned (e.g. PBoC's /en/.../2026/index.html
    archive pages), so the source doesn't silently rot every January.
    """
    hk_now = datetime.now(timezone.utc) + timedelta(hours=8)
    return url.replace("{year}", str(hk_now.year))


# Several sources share one physical URL (e.g. CoinDesk's single RSS feed
# backs three filtered source entries). Cache responses for the lifetime of
# the run so each URL is fetched once per pipeline pass.
_RESPONSE_CACHE = {}


def _get(url, use_cache=True):
    url = resolve_url(url)
    if use_cache and url in _RESPONSE_CACHE:
        return _RESPONSE_CACHE[url]
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECS)
            resp.raise_for_status()
            if use_cache:
                _RESPONSE_CACHE[url] = resp
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
    raise last_exc


def _guess_date(text):
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def is_relevant(*texts):
    haystack = " ".join(t for t in texts if t).lower()
    return any(kw in haystack for kw in RELEVANCE_KEYWORDS)


def _matches_any(haystack, keywords):
    """Word-boundary match -- used for keywords/exclude_keywords/institutional
    lists where entries are short, ambiguous tokens (e.g. "ubs", "visa",
    "swift", "citi"). Plain substring matching would false-positive on
    "hubs"/"clubs", "visas", "swiftly", "cities"/"citizen". Unlike
    RELEVANCE_KEYWORDS (deliberately prefix-matched for tokeni[sz]ation
    etc.), these are meant as whole-word/phrase matches.
    """
    haystack = haystack.lower()
    return any(re.search(r"\b" + re.escape(kw) + r"\b", haystack) for kw in keywords)


def _parse_feed(content, source):
    parsed = feedparser.parse(content)
    items = []
    categories = [c.lower() for c in source.get("categories", [])]
    keywords = [k.lower() for k in source.get("keywords", [])]
    exclude_keywords = [k.lower() for k in source.get("exclude_keywords", [])]

    for entry in parsed.entries:
        url = (entry.get("link") or "").strip()
        title = (entry.get("title") or "").strip()
        summary = (entry.get("summary") or entry.get("description") or "").strip()
        if not url or not title:
            continue

        if categories:
            tags = [t.get("term", "").lower() for t in entry.get("tags", [])]
            if not any(cat in tag for cat in categories for tag in tags):
                continue
        if keywords and not _matches_any(f"{title} {summary}", keywords):
            continue
        if exclude_keywords and _matches_any(f"{title} {summary}", exclude_keywords):
            continue

        published = None
        for key in ("published_parsed", "updated_parsed"):
            struct = entry.get(key)
            if struct:
                published = datetime(*struct[:6], tzinfo=timezone.utc)
                break
        if published is None:
            published = datetime.now(timezone.utc)

        items.append({"title": title, "url": url, "published": published.isoformat(), "summary": summary})
    return items


def _title_text(el):
    """Prefer a nested title-ish element's text over the full element text --
    some cards wrap title + date + teaser paragraph in one giant anchor, and
    a raw get_text() glues them together with no separator (e.g.
    "...function03/07/2026The European...").
    """
    title_el = el.select_one('[class*="title" i], h1, h2, h3, h4, h5, h6')
    if title_el:
        text = title_el.get_text(strip=True)
        if text:
            return text
    return el.get_text(strip=True)


def _extract_page_items(html, base_url, selector=None, href_pattern=None):
    """href_pattern, when given, requires the resolved href to match a regex
    -- for a no-selector fallback page whose nav-menu links otherwise look
    just as plausible as real articles (e.g. a client-rendered SPA shell
    with no actual article markup in server HTML), this is the only way to
    tell "no real content" apart from "some anchor happened to be long
    enough" and correctly report zero items instead of nav junk.
    """
    soup = BeautifulSoup(html, "html.parser")
    if selector:
        # An explicit selector matching zero elements means the page's
        # structure changed -- that must surface as zero items (a health/heal
        # signal), not silently fall back to scraping every <a> tag on the
        # page (nav/boilerplate links), which would mask the breakage as a
        # working source returning junk.
        containers = soup.select(selector)
    else:
        containers = soup.find_all("a")

    items = []
    seen_urls = set()
    for node in containers:
        if node.name == "a":
            link, title = node, _title_text(node)
        else:
            # Prefer the anchor with real text -- a thumbnail/image-only link
            # (no text, alt-text aside) is often the *first* <a> in a card,
            # with the actual title link appearing later in the same node.
            anchors = node.find_all("a", href=True)
            link, title = (anchors[0], "") if anchors else (None, "")
            for candidate in anchors:
                text = _title_text(candidate)
                if len(text) > len(title):
                    link, title = candidate, text
            if link is not None and len(title) < 12:
                # Overlay-anchor card pattern: an empty <a> stretched over the
                # card for click handling, with the real title in a sibling
                # heading rather than inside any anchor.
                title = _title_text(node)
        if not link or not link.get("href"):
            continue
        href = urljoin(base_url, link["href"])
        if href in seen_urls or len(title) < 12:
            continue
        if href_pattern and not re.search(href_pattern, href):
            continue
        seen_urls.add(href)

        published = None
        time_tag = node.find("time") if node.name != "a" else None
        if time_tag and time_tag.get("datetime"):
            try:
                published = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
            except ValueError:
                published = None
        if published is None:
            date_text = node.get_text(" ", strip=True) if node.name != "a" else ""
            match = re.search(
                r"\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}",
                date_text,
            )
            if match:
                published = _guess_date(match.group(0))
        if published is None:
            published = datetime.now(timezone.utc)

        items.append({
            "title": title,
            "url": href,
            "published": published.isoformat(),
            "summary": node.get_text(" ", strip=True) if node.name != "a" else "",
        })
        if len(items) >= 40:
            break
    return items


def fetch_source(source, require_relevant=True):
    """Fetch raw items for one source dict. Returns (items, error).

    require_relevant=False skips the topical keyword gate -- used by heal.py
    to validate that a candidate URL is structurally scrapable without a
    same-day topical match masking an otherwise-working source.
    """
    try:
        resp = _get(source["url"])
    except requests.RequestException as exc:
        return [], f"fetch failed: {exc}", 0

    try:
        if source["kind"] == "feed":
            items = _parse_feed(resp.content, source)
        else:
            items = _extract_page_items(
                resp.text, source["url"], source.get("selector"), source.get("href_pattern")
            )
    except Exception as exc:  # a parsing bug in one source must not kill the run
        return [], f"parse failed: {exc}", 0

    raw_count = len(items)  # pre-relevance-filter -- a healthy source with no
    # crypto news today must not look like a dead one to heal.py.
    if require_relevant:
        items = [it for it in items if is_relevant(it["title"], it.get("summary", ""))]

    for item in items:
        item["source"] = source["name"]
        item["jurisdiction"] = source["jurisdiction"]
        item["tier"] = source["tier"]

    return items, None, raw_count


def fetch_all(sources):
    """Fetch items for every non-register source.
    Returns dict name -> {items, error, raw_count}.
    """
    results = {}
    for source in sources:
        if source.get("kind") == "register":
            continue
        items, error, raw_count = fetch_source(source)
        results[source["name"]] = {"items": items, "error": error, "raw_count": raw_count}
        logger.info(
            "fetched %s: %d items (raw %d)%s",
            source["name"], len(items), raw_count, f" ({error})" if error else "",
        )
    return results
