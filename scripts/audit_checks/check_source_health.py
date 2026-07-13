"""SOFT. Reconciles data/source-health.json against data/sources.json: a
source rename silently orphans its health-tracking row and starts a FRESH
one at consecutive_failures=0 -- deferring self-heal by up to
heal.FAILURE_THRESHOLD more runs for a source that may already be broken.
Also flags sources sitting just below the self-heal threshold, so a
slow-motion failure is visible before it actually trips.
"""
import json

from base import finding, could_not_run

CHECK_ID = "source_health_reconciliation"
MODE = "soft"


def run(repo_root):
    try:
        sources = json.loads((repo_root / "data" / "sources.json").read_text(encoding="utf-8"))
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/sources.json or digest.json: {exc}")]

    try:
        import sys
        sys.path.insert(0, str(repo_root / "scripts"))
        import heal as heal_mod
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import scripts/heal.py: {exc}")]

    source_names = {s["name"] for s in sources}
    health_names = {h["name"] for h in digest.get("source_health", []) if h.get("name") != "Claude summarisation"}

    findings = []
    orphaned = health_names - source_names
    for name in sorted(orphaned):
        findings.append(finding(
            CHECK_ID, "warn", f"orphaned source-health row: '{name}'",
            "This name in the published source_health list matches no current entry in "
            "data/sources.json -- likely a rename. The renamed source will start self-heal counting "
            "from zero, silently deferring detection of a real outage.",
            {"name": name},
        ))

    missing = source_names - health_names
    for name in sorted(missing):
        findings.append(finding(
            CHECK_ID, "info", f"source with no health row yet: '{name}'",
            "Present in data/sources.json but not yet reported in source_health -- expected for a "
            "brand-new source on its first run, otherwise worth a look.",
            {"name": name},
        ))

    # data/source-health.json (the raw failure-counter file, separate from
    # the digest's rendered snapshot) carries consecutive_failures directly.
    health_path = repo_root / "data" / "source-health.json"
    if health_path.exists():
        try:
            raw_health = json.loads(health_path.read_text(encoding="utf-8"))
        except Exception as exc:
            findings.append(finding(CHECK_ID, "warn", "data/source-health.json is not valid JSON", str(exc), {}))
            raw_health = {}
        threshold = heal_mod.FAILURE_THRESHOLD
        for name, state in raw_health.items():
            n = state.get("consecutive_failures", 0)
            if threshold - 2 <= n < threshold:
                findings.append(finding(
                    CHECK_ID, "warn", f"'{name}' is {n}/{threshold} consecutive failures from self-heal",
                    f"consecutive_failures={n}, last_status={state.get('last_status')}. Worth checking "
                    "manually before it silently trips auto-heal (or gets marked dead if keyless).",
                    {"name": name, "consecutive_failures": n, "threshold": threshold},
                ))

    return findings
