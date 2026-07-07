"""Official-register snapshot + diff (e.g. SFC's list of licensed VATPs)."""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from fetch import _get

REGISTERS_DIR = Path(__file__).resolve().parent.parent / "data" / "registers"

# A diff bigger than this fraction of the previous snapshot is treated as an
# extraction failure (page redesign, column reorder), NOT as news -- otherwise
# one layout change fires a flood of bogus official "licensing" items that
# monopolise the daily item cap and permanently corrupt the baseline.
MAX_CHURN_FRACTION = 0.5
MAX_CHURN_FLOOR = 5  # small registers: always allow up to this many changes

# Skip obvious header/nav rows and cells that are clearly not entity names.
_SKIP_TEXT = {"", "ce reference", "company name", "platform name", "licence date",
              "license date", "application date", "closure deadline", "english", "chinese"}
_SKIP_SUBSTRINGS = ("company name",)


def _slug(name):
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        # A fully non-Latin name (e.g. a Chinese company name) slugs to "" --
        # two such entities added the same day would share one id and the
        # second event would be silently dropped by dedupe. Hash instead.
        slug = hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]
    return slug


def _extract_entities(html, selector=None, column=None):
    """column, when given, picks a specific 0-indexed <td>/<th> out of each
    <tr> (e.g. the English company-name column) instead of the first cell --
    register tables commonly lead with a reference code column, not the name.
    """
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select(selector) if selector else soup.find_all(["tr", "li"])
    names = []
    seen = set()
    for node in nodes:
        if node.name == "tr" and column is not None:
            cells = node.find_all(["td", "th"])
            if len(cells) <= column:
                # A row without the expected column (header/spacer row, or a
                # table redesign) must be skipped, not fall back to whole-row
                # text -- that would fabricate a phantom "entity" and fire a
                # bogus licensing diff.
                continue
            cell = cells[column]
        elif node.name == "tr":
            cell = node.find(["td", "th"])
        else:
            cell = node
        text = (cell or node).get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) < 3 or len(text) > 200:
            continue
        if text.lower() in _SKIP_TEXT or any(s in text.lower() for s in _SKIP_SUBSTRINGS):
            continue
        if text in seen:
            continue
        seen.add(text)
        names.append(text)
    return names


def diff_register(source):
    """Fetch a register source, diff against the last snapshot.

    Returns (added, removed, error). On the very first run for a source
    (no prior snapshot), returns no diff items -- only a baseline is stored,
    otherwise every existing licensee would fire as "newly added".
    """
    REGISTERS_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = REGISTERS_DIR / f"{_slug(source['name'])}.json"
    first_run = not snapshot_path.exists()

    try:
        resp = _get(source["url"])
        current = _extract_entities(resp.text, source.get("selector"), source.get("column"))
    except Exception as exc:
        return [], [], f"register fetch/parse failed: {exc}"

    if not current:
        return [], [], "register returned zero entities"

    previous = []
    if not first_run:
        try:
            previous = json.loads(snapshot_path.read_text(encoding="utf-8")).get("entities", [])
        except (ValueError, OSError):
            # A truncated/corrupt snapshot must not kill the whole daily run.
            # Re-baseline like a first run (no events) and heal the file.
            first_run = True

    added = [] if first_run else sorted(set(current) - set(previous))
    removed = [] if first_run else sorted(set(previous) - set(current))

    # Mass-change guard: a real register changes a few entries at a time. A
    # wholesale diff means the extraction shifted (redesign/column reorder) --
    # publishing it would flood the digest with bogus official items. Leave
    # the snapshot UNTOUCHED so a fixed selector diffs against the last good
    # baseline instead of a corrupted one.
    if previous:
        churn_limit = max(MAX_CHURN_FLOOR, int(len(previous) * MAX_CHURN_FRACTION))
        if len(added) + len(removed) > churn_limit:
            return [], [], (
                f"register diff implausibly large ({len(added)} added, {len(removed)} removed "
                f"vs {len(previous)} baseline) — layout change? Snapshot kept; verify manually"
            )

    # Known tradeoff: the snapshot is updated NOW, before run.py's item cap
    # runs, so a register event that later falls past the 25-item cap would
    # not refire. In practice register items sort official-tier + newest and
    # land at the top of the cap; accepted for simplicity.

    # Atomic write: a run killed mid-write must not leave a truncated snapshot.
    tmp_path = snapshot_path.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(
            {"entities": current, "updated_at": datetime.now(timezone.utc).isoformat()},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    os.replace(tmp_path, snapshot_path)

    return added, removed, None


def register_items(source, added, removed):
    """Build pipeline-shaped raw items for register additions/removals.

    Ids carry the event date: an entity removed and later RE-added (e.g. a
    licence suspension then reinstatement) must surface as a fresh event, not
    be swallowed by the dedupe memory of the original addition. Same-day
    duplicates are impossible -- the snapshot updates in the same run, so a
    given diff only ever fires once.
    """
    now = datetime.now(timezone.utc).isoformat()
    day = now[:10]
    items = []
    for name in added:
        items.append({
            "id": f"register-{_slug(source['name'])}-{_slug(name)}-add-{day}",
            "jurisdiction": source["jurisdiction"],
            "source": source["name"],
            "title": f"{name} added to {source['name']}",
            "url": source["url"],
            "published": now,
            "type": "licensing",
            "tier": "official",
            "summary": f"{name} newly appears on the {source['name']}.",
        })
    for name in removed:
        items.append({
            "id": f"register-{_slug(source['name'])}-{_slug(name)}-remove-{day}",
            "jurisdiction": source["jurisdiction"],
            "source": source["name"],
            "title": f"{name} removed from {source['name']}",
            "url": source["url"],
            "published": now,
            "type": "licensing",
            "tier": "official",
            "summary": f"{name} no longer appears on the {source['name']}.",
        })
    return items


def run_registers(sources):
    """Process every kind=register source. Returns (items, health_notes)."""
    items = []
    health_notes = []
    for source in sources:
        if source.get("kind") != "register":
            continue
        try:
            added, removed, error = diff_register(source)
        except Exception as exc:  # one register must never kill the whole run
            added, removed, error = [], [], f"register processing failed: {exc}"
        if error:
            health_notes.append({"name": source["name"], "status": "dead", "note": error})
            continue
        items.extend(register_items(source, added, removed))
        if added or removed:
            health_notes.append({
                "name": source["name"],
                "status": "ok",
                "note": f"{len(added)} added, {len(removed)} removed this run",
            })
        else:
            health_notes.append({"name": source["name"], "status": "ok", "note": "No change"})
    return items, health_notes
