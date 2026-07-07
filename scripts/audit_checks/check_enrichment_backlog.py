"""SOFT. Detects items the weekday enrichment session should have touched
but didn't -- summary==title or a so_what that never got past its fallback
text means recipe steps 2-3 (summarise/classify) were skipped; a
date_source still 'fetch_time' after several days means step 7
(date-and-fact-check) was skipped. Report-only: this never rewrites content,
since only the model actually reading the source should write a summary.
"""
import json
from datetime import datetime, timezone

from base import finding, could_not_run

CHECK_ID = "enrichment_backlog"
MODE = "soft"

STALE_UNENRICHED_DAYS = 2       # calendar-day proxy for "1 weekday" (weekends included, simpler than a calendar)
STALE_FETCH_TIME_DAYS = 4       # proxy for "3 weekdays"
FALLBACK_SO_WHAT_PREFIX = "Review the source directly"


def run(repo_root):
    try:
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/digest.json: {exc}")]

    now = datetime.now(timezone.utc)
    findings = []
    unenriched, stale_dates = [], []

    for it in digest.get("items", []):
        try:
            fs = datetime.fromisoformat(it["first_seen"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        age_days = (now - fs).days

        if age_days >= STALE_UNENRICHED_DAYS and (
            it.get("summary") == it.get("title")
            or str(it.get("so_what", "")).startswith(FALLBACK_SO_WHAT_PREFIX)
        ):
            unenriched.append((it.get("id"), it.get("title", "")[:60], age_days))

        if age_days >= STALE_FETCH_TIME_DAYS and it.get("date_source") == "fetch_time":
            stale_dates.append((it.get("id"), it.get("title", "")[:60], age_days))

    if unenriched:
        findings.append(finding(
            CHECK_ID, "warn", f"{len(unenriched)} item(s) still unenriched after {STALE_UNENRICHED_DAYS}+ days",
            f"summary==title or a fallback so_what persisted past the next enrichment session's window: "
            f"{unenriched}",
            {"items": unenriched},
        ))
    if stale_dates:
        findings.append(finding(
            CHECK_ID, "warn", f"{len(stale_dates)} item(s) still date_source=fetch_time after {STALE_FETCH_TIME_DAYS}+ days",
            f"The date-and-fact-check pass (CLAUDE.md step 7) should have resolved these to a real "
            f"publication date or left them explicitly unresolved: {stale_dates}",
            {"items": stale_dates},
        ))
    return findings
