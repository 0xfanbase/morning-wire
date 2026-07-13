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

# Backlog gate for a *daily* digest: feeds and listing pages return their most
# recent N entries regardless of age (some HKMA RSS feeds carry a year of
# history), so a source's first-ever run -- or any newly added source -- would
# otherwise flood the digest with months-old items presented as today's news.
# 7 matches the page's priority-strip window exactly: anything the gate admits
# is still young enough to headline on the day it is first seen.
MAX_ITEM_AGE_DAYS = 7

# Slash dates are inherently ambiguous; %m/%d/%Y is tried FIRST because the
# only sources in the registry that render slash dates are US ones (FinCEN,
# OFAC) -- with %d/%m/%Y first, "07/01/2026" (July 1, US) would parse as
# 7 January and be silently dropped by the age gate as stale.
DATE_FORMATS = [
    "%Y-%m-%d",
    "%d %B %Y",
    "%B %d, %Y",
    "%d %b %Y",
    "%b %d, %Y",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%Y/%m/%d",
]

# Simple topical gate so general-mandate regulator feeds (bank supervision,
# futures, securities-at-large) don't flood the digest with non-digital-asset
# items. Crypto-native outlets (CoinDesk/The Block) pass this trivially.
# Substring-matched: multiword phrases and deliberate prefixes ("tokeni"
# catches tokenise/tokenized/tokenisation; "stablecoin" catches plurals).
RELEVANCE_KEYWORDS = [
    "crypto", "digital asset", "digital-asset", "virtual asset", "stablecoin",
    "stable coin", "tokeni", "blockchain", "distributed ledger",
    "vasp", "web3", "web 3", "bitcoin",
    "ethereum", "e-cny", "ecny", "cbdc", "travel rule",
    "virtual currency", "defi", "decentralized finance", "decentralised finance",
    "crypto mixer", "crypto-asset", "cryptoasset",
    "e-hkd", "digital yuan", "digital renminbi", "digital currency",
    "project ensemble", "mbridge", "m-bridge", "wallet", "self-custody",
    "cyber-related",
]
# Word-boundary-matched: short/ambiguous tokens that substring matching would
# false-positive on ("mica" is inside chemical/economically/dynamically,
# "casp" inside Caspian, "ether" inside whether/together, "dlt"/"btc"/"nft"
# appear inside other codes). Also named assets whose headlines carry no
# generic crypto term ("Ether ETF", Ripple/XRP, Solana).
RELEVANCE_WORD_KEYWORDS = [
    "mica", "casp", "vatp", "dpt", "dlt", "btc", "nft",
    "ether", "xrp", "ripple", "solana",
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


HKT = timezone(timedelta(hours=8))


def _guess_date(text):
    """Parse a date-only string. Anchored to midnight HONG KONG time, not UTC:
    midnight UTC is 08:00 HKT, which the page would render as a fabricated
    "08:00" timestamp on every date-only card -- and the template deliberately
    hides a "00:00" HKT time as date-only noise."""
    text = text.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=HKT)
        except ValueError:
            continue
    return None


def is_relevant(*texts):
    haystack = " ".join(t for t in texts if t).lower()
    if any(kw in haystack for kw in RELEVANCE_KEYWORDS):
        return True
    return _matches_any(haystack, RELEVANCE_WORD_KEYWORDS)


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
    """Structurally parse a feed into items (url + title present). Topic
    filtering (source-level categories/keywords/exclude_keywords) is applied
    LATER, in fetch_source behind require_relevant -- so the count this returns
    reflects structural health, not how much on-topic news the feed carried
    today. A working feed with no China-keyword items this week must NOT look
    dead to heal.py (which would eventually mark it dead or auto-rewrite it).
    Entry tags ride along as a private `_tags` field for the deferred category
    gate and are stripped before the item leaves fetch_source.
    """
    parsed = feedparser.parse(content)
    items = []
    for entry in parsed.entries:
        url = (entry.get("link") or "").strip()
        title = _clean_title(entry.get("title") or "")
        summary = (entry.get("summary") or entry.get("description") or "").strip()
        if not url or not title:
            continue

        published = None
        for key in ("published_parsed", "updated_parsed"):
            struct = entry.get(key)
            if struct:
                published = datetime(*struct[:6], tzinfo=timezone.utc)
                break
        # date_source is provenance for the DATE itself: "fetch_time" marks a
        # fallback stamp so the enrichment session knows to fact-check the real
        # publication date from the article and set it to "verified".
        date_source = "feed"
        if published is None:
            published = datetime.now(timezone.utc)
            date_source = "fetch_time"

        tags = [t.get("term", "").lower() for t in entry.get("tags", [])]
        items.append({"title": title, "url": url, "published": published.isoformat(),
                      "date_source": date_source, "summary": summary, "_tags": tags})
    return items


def _feed_topic_ok(item, source):
    """Source-level topic gate for one feed item: `categories` must match an
    entry tag, `exclude_keywords` must not match, and the item must pass
    the source's own topic test -- either its named `keywords` list, or (see
    `role_keywords`/`action_keywords` below) BOTH a generic institution-role
    term and a digital-asset-specific action term. Applied only when
    relevance filtering is on, so it never affects the structural raw count.
    Page items (no `_tags`, no keyword config) pass.

    `role_keywords` + `action_keywords`: a named-entity list can only ever
    catch institutions someone thought to enumerate in advance -- the next
    bank to open a tokenisation desk is never on it. This pairs a GENERIC
    institution-type term (bank, asset manager, custodian, exchange
    operator...) with a digital-asset-specific activity term (tokeniz,
    stablecoin, crypto custody...) so a peer's move is caught by WHAT it is
    and WHAT it did, not by WHO it is. Both lists must have at least one hit;
    an item can also still pass via the plain `keywords` list (kept for the
    handful of institutions -- payment/card networks -- whose own name
    doesn't self-describe as a "bank" or "asset manager").
    """
    categories = [c.lower() for c in source.get("categories", [])]
    keywords = [k.lower() for k in source.get("keywords", [])]
    exclude_keywords = [k.lower() for k in source.get("exclude_keywords", [])]
    role_keywords = [k.lower() for k in source.get("role_keywords", [])]
    action_keywords = [k.lower() for k in source.get("action_keywords", [])]
    text = f"{item['title']} {item.get('summary', '')}"
    if categories:
        tags = item.get("_tags", [])
        if not any(cat in tag for cat in categories for tag in tags):
            return False
    has_topic_gate = bool(keywords) or bool(role_keywords and action_keywords)
    if has_topic_gate:
        named_hit = bool(keywords) and _matches_any(text, keywords)
        # role_keywords is word-boundary matched (short generic words like
        # "bank" must not fire inside "bankruptcy"/"embankment"). action_
        # keywords is plain substring matched -- deliberately, so a prefix
        # like "tokeniz" also catches "tokenized"/"tokenizing"/"tokenization"
        # (same reasoning as RELEVANCE_KEYWORDS above); these are compound
        # enough phrases that substring matching carries little false-hit risk.
        haystack = text.lower()
        role_hit = (bool(role_keywords and action_keywords)
                    and _matches_any(text, role_keywords)
                    and any(kw in haystack for kw in action_keywords))
        if not (named_hit or role_hit):
            return False
    if exclude_keywords and _matches_any(text, exclude_keywords):
        return False
    return True


_ZERO_WIDTH_RE = re.compile(r"[​‌‍﻿]")
_PDF_SUFFIX_RE = re.compile(r"\s*\(PDF File[^)]*\)\s*$", re.IGNORECASE)


def _clean_title(text):
    """Normalise scraped titles: strip zero-width characters some CMSes leak
    into headlines (EBA), collapse whitespace, and drop trailing
    "(PDF File, 142.8 KB)"-style attachment suffixes (HKMA circulars).
    """
    text = _ZERO_WIDTH_RE.sub("", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return _PDF_SUFFIX_RE.sub("", text)


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
                # heading. Only accept an explicit heading/title element here
                # -- falling back to the container's full text would glue
                # date + teaser into a junk headline.
                heading = node.select_one('[class*="title" i], h1, h2, h3, h4, h5, h6')
                title = heading.get_text(" ", strip=True) if heading else ""
        title = _clean_title(title)
        if not link or not link.get("href"):
            continue
        href = urljoin(base_url, link["href"])
        if href in seen_urls or len(title) < 12:
            continue
        if href_pattern and not re.search(href_pattern, href):
            continue
        seen_urls.add(href)

        published = None
        # Bare-anchor sources (href_pattern mode: ACAMS, TRM...) have no card
        # container, so look for the date in the anchor's PARENT card element
        # -- stamping published=now() would defeat the backfill age gate for
        # exactly the sources that need it most.
        date_scope = node if node.name != "a" else (node.parent or node)
        time_tag = date_scope.find("time")
        if time_tag and time_tag.get("datetime"):
            try:
                raw = time_tag["datetime"].strip()
                published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if published.tzinfo is None:
                    # Offset-less values embed as naive strings the browser
                    # would read in the VIEWER'S timezone -- dates would shift
                    # per reader. Date-only values anchor to midnight HKT
                    # (consistent with _guess_date); date-times assume UTC.
                    published = published.replace(tzinfo=HKT if len(raw) == 10 else timezone.utc)
            except ValueError:
                published = None
        if published is None:
            date_text = date_scope.get_text(" ", strip=True)
            match = re.search(
                r"\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}",
                date_text,
            )
            if match:
                published = _guess_date(match.group(0))
        date_source = "page"
        if published is None:
            published = datetime.now(timezone.utc)
            date_source = "fetch_time"

        items.append({
            "title": title,
            "url": href,
            "published": published.isoformat(),
            "date_source": date_source,
            "summary": node.get_text(" ", strip=True) if node.name != "a" else "",
        })
        if len(items) >= 40:
            break
    return items


_LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
_ARTICLE_DATE_RE = re.compile(
    r"\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}"
)


def _extract_sitemap_urls(xml_text, href_pattern, cap=200):
    """Pull candidate article URLs straight out of a sitemap.xml -- for a
    listing page whose article links only ever appear after client-side JS
    runs (e.g. MAS's "News" page), the sitemap is the only place a plain
    HTTP fetch can see them. A plain regex is enough (and avoids pulling in
    an XML parser for one flat <loc> list) and href_pattern reuses the same
    filter _extract_page_items applies, so only the listing this source
    actually cares about (e.g. this year's media releases) is kept.

    The cap must comfortably exceed a full YEAR of matching pages, because
    sitemap order carries no recency signal to prefer (MAS's is
    alphabetical, with no <lastmod>): with a too-small cap, the year's
    cumulative pages eventually push the newest ones past the cutoff and
    the source silently stops seeing new items while still looking healthy
    (raw_count > 0). MAS had already accumulated 56 matching pages by
    mid-July 2026; 200 covers a year with margin. Hitting the cap is
    logged, since it means exactly that silent-miss risk has arrived.
    """
    urls = []
    seen = set()
    for loc in _LOC_RE.findall(xml_text):
        loc = loc.strip()
        if loc in seen or not re.search(href_pattern, loc):
            continue
        seen.add(loc)
        urls.append(loc)
        if len(urls) >= cap:
            logger.warning(
                "sitemap has more than %d urls matching %r -- later matches are "
                "silently skipped and new articles may be missed; raise the cap",
                cap, href_pattern,
            )
            break
    return urls


def _extract_article_title_date(html):
    """Title + publish date for one article page, for sources fetched via
    _extract_sitemap_urls. Regulator press releases bury unrelated dates deep
    in the body (enforcement-case tables, appointment-term tables), so the
    date search window starts at the first non-empty <h1> (the real headline,
    as opposed to nav/menu junk earlier in the DOM) and only looks at the text
    immediately following it -- which is where a press release's own dateline
    ("Singapore, 1 July 2026...") lives.
    """
    soup = BeautifulSoup(html, "html.parser")
    h1 = next((h for h in soup.find_all("h1") if h.get_text(strip=True)), None)
    if h1 is None:
        return None, None
    title = _clean_title(h1.get_text(" ", strip=True))
    window_parts = []
    window_len = 0
    for node in h1.find_all_next(string=True):
        text = str(node).strip()
        if not text:
            continue
        window_parts.append(text)
        window_len += len(text)
        if window_len > 2000:
            break
    match = _ARTICLE_DATE_RE.search(" ".join(window_parts))
    published = _guess_date(match.group(0)) if match else None
    return title, published


# Bail-outs for _fetch_sitemap_items: each dead article URL costs the full
# retry ladder ((MAX_RETRIES+1) x TIMEOUT_SECS plus backoff sleeps, ~50s), so
# a site that serves its sitemap but times out on articles would otherwise
# burn ~50s x every candidate URL -- enough to blow digest.yml's whole
# 30-minute job timeout on this one source and kill the day's run outright.
# Consecutive failures end the sweep early (the site is telling us it won't
# serve articles right now); the elapsed budget separately bounds a
# slow-but-not-failing crawl. Either exit leaves a normal partial result:
# zero items keeps the health-check failure signal, some items count as a
# working source.
MAX_SITEMAP_CONSECUTIVE_FAILURES = 3
SITEMAP_TIME_BUDGET_SECS = 300


def _fetch_sitemap_items(sitemap_url, href_pattern):
    """Fallback for a "page" source whose primary listing URL returns zero
    structural items -- fetch each candidate article directly instead. This
    is deliberately only a fallback (see fetch_source): most page sources
    scrape fine from the listing itself, and hitting every article URL on
    every run is real extra load that's only worth it when the listing page
    has nothing to scrape.
    """
    resp = _get(sitemap_url)
    urls = _extract_sitemap_urls(resp.text, href_pattern)
    items = []
    consecutive_failures = 0
    started = time.monotonic()
    for url in urls:
        if consecutive_failures >= MAX_SITEMAP_CONSECUTIVE_FAILURES:
            logger.warning(
                "sitemap sweep of %s aborted after %d consecutive article fetch "
                "failures (%d item(s) collected so far)",
                sitemap_url, consecutive_failures, len(items),
            )
            break
        if time.monotonic() - started > SITEMAP_TIME_BUDGET_SECS:
            logger.warning(
                "sitemap sweep of %s stopped at its %ds time budget (%d item(s) collected)",
                sitemap_url, SITEMAP_TIME_BUDGET_SECS, len(items),
            )
            break
        try:
            article = _get(url)
        except requests.RequestException:
            consecutive_failures += 1
            continue
        consecutive_failures = 0
        title, published = _extract_article_title_date(article.text)
        if not title:
            continue
        date_source = "page"
        if published is None:
            published = datetime.now(timezone.utc)
            date_source = "fetch_time"
        items.append({
            "title": title,
            "url": url,
            "published": published.isoformat(),
            "date_source": date_source,
            "summary": "",
        })
    return items


def _is_recent(item):
    """True when the item's published date is within MAX_ITEM_AGE_DAYS.
    Unparseable dates pass -- both parsers fall back to now() anyway, and a
    date bug must degrade to 'maybe stale' rather than 'silently dropped'."""
    try:
        published = datetime.fromisoformat(item["published"])
    except (KeyError, TypeError, ValueError):
        return True
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - published <= timedelta(days=MAX_ITEM_AGE_DAYS)


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
            # base_url and href_pattern must both be RESOLVED: joining relative
            # hrefs against a literal "{year}" path would publish broken links,
            # and a {year}-scoped href_pattern (e.g. MAS media-releases) must
            # match the current year, not the literal token.
            href_pattern = source.get("href_pattern")
            if href_pattern:
                href_pattern = resolve_url(href_pattern)
            items = _extract_page_items(
                resp.text, resolve_url(source["url"]), source.get("selector"), href_pattern
            )
            # A listing page that renders its article list via client-side JS
            # (nothing in the raw HTML for BeautifulSoup to see) structurally
            # returns zero items every single run -- sitemap_url is only
            # configured for sources known to need this, so this never fires
            # for an ordinary page source that's just having a quiet news day.
            if not items and source.get("sitemap_url") and href_pattern:
                items = _fetch_sitemap_items(source["sitemap_url"], href_pattern)
    except Exception as exc:  # a parsing bug in one source must not kill the run
        return [], f"parse failed: {exc}", 0

    raw_count = len(items)  # structural (url+title present), BEFORE any topic
    # filtering -- a healthy source with no crypto news today, or a filtered
    # feed with no keyword hits this week, must not look dead to heal.py.
    if require_relevant:
        # Topic gate (source keyword/category), global relevance gate, and the
        # age gate all live behind the same flag: heal.py validates candidate
        # replacement URLs structurally with require_relevant=False, so a
        # working source whose latest items are off-topic or old must validate.
        # A source with its OWN curated keyword list (Institutional moves,
        # Market news, China policy) is its own relevance filter: "Coinbase
        # wins dismissal" or "Saylor's Strategy sells bitcoin" matches the
        # source definition but contains no generic crypto term, and the
        # global gate must not veto what the source was built to admit.
        own_keywords = bool(source.get("keywords")) or bool(
            source.get("role_keywords") and source.get("action_keywords")
        )
        items = [
            it for it in items
            if _feed_topic_ok(it, source)
            and (own_keywords or is_relevant(it["title"], it.get("summary", "")))
            and _is_recent(it)
        ]

    for item in items:
        item.pop("_tags", None)  # private parse-time field, never surfaced
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
