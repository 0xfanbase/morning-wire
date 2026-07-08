"""SOFT. Flags items tagged jurisdiction=GLOBAL whose own title strongly
signals one specific jurisdiction's regulator/agency -- e.g. an industry
blog's write-up of an OFAC sanctions action is fundamentally US news, even
though the blog itself (TRM Labs, Chainalysis, Elliptic...) is configured
GLOBAL in data/sources.json. The source's default jurisdiction is a fetch-
time convenience, not the ground truth for what the STORY is about; this
check is a reminder for the human/enrichment session to retag on a
case-by-case basis, never an automatic rewrite (a false-positive keyword
match must not silently relabel a genuinely global story).
"""
import json
import re

from base import finding, could_not_run

CHECK_ID = "item_jurisdiction_signal"
MODE = "soft"

# Regulator/agency names strong enough to imply a specific jurisdiction even
# when the reporting source itself is tagged GLOBAL. Deliberately short and
# high-precision (full agency names or their common short forms) -- this is
# a nudge for human review, not a classifier, so false positives cost a
# wasted glance while false negatives just mean business-as-usual.
JURISDICTION_SIGNALS = {
    "US": [r"\bOFAC\b", r"\bFinCEN\b", r"\bSEC\b", r"\bCFTC\b", r"\bDOJ\b",
           r"\bTreasury\b", r"\bFederal Reserve\b", r"\bOCC\b", r"\bIRS\b"],
    "HK": [r"\bHKMA\b", r"\bSFC\b", r"\bFSTB\b", r"\bHong Kong\b"],
    "CN": [r"\bPBOC\b", r"\bPeople's Bank of China\b", r"\bCSRC\b"],
    "SG": [r"\bMAS\b", r"\bMonetary Authority of Singapore\b"],
    "EU": [r"\bESMA\b", r"\bEBA\b", r"\bMiCA\b", r"\bEuropean (Banking|Securities)\b"],
    "UK": [r"\bFCA\b", r"\bFinancial Conduct Authority\b", r"\bBank of England\b",
           r"\bHM Treasury\b", r"\bPRA\b", r"\bPrudential Regulation Authority\b"],
}


def run(repo_root):
    try:
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/digest.json: {exc}")]

    findings = []
    for it in digest.get("items", []):
        if it.get("jurisdiction") != "GLOBAL":
            continue
        title = it.get("title", "")
        for juris, patterns in JURISDICTION_SIGNALS.items():
            if any(re.search(p, title, re.IGNORECASE) for p in patterns):
                findings.append(finding(
                    CHECK_ID, "info",
                    f"possible jurisdiction mistag: '{title[:70]}'",
                    f"Tagged GLOBAL but the title matches a {juris}-specific regulator/agency "
                    f"signal. Consider retagging to {juris} during the next enrichment pass if "
                    "the story is genuinely about that jurisdiction's action -- never auto-rewrite.",
                    {"id": it.get("id"), "title": title, "suggested_jurisdiction": juris},
                ))
                break  # one suggestion per item is enough
    return findings
