"""SOFT, but with a HARD-flavoured guardrail baked in. A jurisdiction with
zero live official sources is a structural coverage hole -- distinct from a
jurisdiction merely having no crypto news today (normal; MAS routinely
contributes 0 relevant items while still being reachable). This check
judges LIVENESS (source_health status), never item volume, so it can't be
fooled by a quiet news day into crying wolf, and can't be silenced by a
source quietly having nothing to report.

Guardrail: official-tier and register sources, and any source that is its
jurisdiction's ONLY official source, may never appear in
audit/baseline.json's human-maintained accepted_dead list. That list exists
for known, permanent, non-fixable blocks (FATF's Cloudflare 403) on
industry-tier or redundantly-covered sources only -- this check verifies
the humans maintaining that file haven't violated their own policy.
"""
import json

from base import finding, could_not_run

CHECK_ID = "jurisdiction_coverage"
MODE = "soft"

JURISDICTIONS = ["HK", "CN", "US", "EU", "SG"]  # GLOBAL excluded: no single-jurisdiction bar applies


def run(repo_root):
    try:
        sources = json.loads((repo_root / "data" / "sources.json").read_text(encoding="utf-8"))
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/sources.json or digest.json: {exc}")]

    health_by_name = {h["name"]: h for h in digest.get("source_health", [])}
    findings = []

    for j in JURISDICTIONS:
        official = [s for s in sources if s["jurisdiction"] == j and s.get("tier") == "official"]
        if not official:
            continue  # no official source configured for this jurisdiction at all -- a design gap, not this check's job
        alive = [s for s in official
                 if health_by_name.get(s["name"], {}).get("status") not in ("dead",)]
        if not alive:
            findings.append(finding(
                CHECK_ID, "warn",
                f"{j}: every official source shows status=dead",
                f"{len(official)} official source(s) configured for {j}, all currently reported dead: "
                f"{[s['name'] for s in official]}. This jurisdiction has no live official coverage.",
                {"jurisdiction": j, "dead_sources": [s["name"] for s in official]},
            ))

    # Guardrail check on the human-maintained accepted_dead list.
    baseline_path = repo_root / "audit" / "baseline.json"
    if baseline_path.exists():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return findings + [finding(CHECK_ID, "warn", "audit/baseline.json is not valid JSON", str(exc), {})]

        source_by_name = {s["name"]: s for s in sources}
        for entry in baseline.get("accepted_dead", []):
            name = entry.get("source")
            s = source_by_name.get(name)
            if not s:
                continue
            same_juris_official = [x for x in sources if x["jurisdiction"] == s["jurisdiction"] and x.get("tier") == "official"]
            if s.get("tier") == "official" or s.get("kind") == "register" or len(same_juris_official) <= 1:
                findings.append(finding(
                    CHECK_ID, "critical",
                    f"accepted_dead policy violation: '{name}'",
                    f"audit/baseline.json accepts '{name}' as a known-dead exception, but it is "
                    f"tier={s.get('tier')} kind={s.get('kind')} and is one of only "
                    f"{len(same_juris_official)} official source(s) for {s['jurisdiction']}. "
                    "Official/register sources and a jurisdiction's sole official source may never "
                    "be baselined dead -- this entry should be removed from accepted_dead.",
                    {"source": name, "entry": entry},
                ))
    return findings
