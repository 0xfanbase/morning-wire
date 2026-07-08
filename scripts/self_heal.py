"""Standalone self-heal sweep: fetch + register-diff + health-check/heal,
without the rest of scripts/run.py's pipeline (dedupe/verify/summarise, or
any write to data/seen-items.json or digest.json's `items`).

Exists so the weekly integrity-audit routine (audit/PLAYBOOK.md Phase 0.5)
can give every configured source a fresh, real liveness check and a chance
at Claude-assisted repair, independent of whether that day's automated
daily run (scripts/run.py, via .github/workflows/digest.yml) had
ANTHROPIC_API_KEY available. Requires a real network fetch against every
configured source -- there is no cached artifact this can replay from
already-committed data.

Also updates data/digest.json's `source_health` field and re-renders
docs/index.html/docs/feed.xml: check_jurisdiction_coverage.py and
check_source_health.py both read source health from THAT field, not from
data/source-health.json's raw failure counters directly, and the public
Source health tab reads it too -- without this, a sweep that revives or
kills a source would be invisible everywhere except the raw counter file
until the next real daily run.

Run manually: python3 scripts/self_heal.py
"""
import json
import logging
from pathlib import Path

import fetch
import heal
import registers
import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("self_heal")

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "data" / "sources.json"
DIGEST_PATH = ROOT / "data" / "digest.json"


def main():
    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8"))

    logger.info("1/3 fetch")
    fetch_results = fetch.fetch_all(sources)

    logger.info("2/3 register diff")
    _register_items, register_health_notes = registers.run_registers(sources)

    logger.info("3/3 health check + self-heal")
    source_health = heal.health_check_and_heal(sources, fetch_results, register_health_notes)
    SOURCES_PATH.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")

    digest = json.loads(DIGEST_PATH.read_text(encoding="utf-8"))
    # Preserve any non-source rows the daily pipeline appends (e.g. the
    # "Claude summarisation" row) that this sweep never touches.
    other_rows = [h for h in digest.get("source_health", [])
                  if h.get("name") not in {s["name"] for s in source_health}]
    digest["source_health"] = source_health + other_rows
    render.render(digest)
    DIGEST_PATH.write_text(json.dumps(digest, indent=2, ensure_ascii=False), encoding="utf-8")

    healed = [h for h in source_health if h.get("status") == "replaced"]
    dead = [h for h in source_health if h.get("status") == "dead"]
    logger.info(f"done: {len(source_health)} sources checked, {len(healed)} healed, {len(dead)} dead")


if __name__ == "__main__":
    main()
