"""SOFT, needs-human only. A curated set of numeric claims in README.md /
CLAUDE.md that this project has drifted on before (the backlog-cap comment
said "10 days" for weeks after the code changed to 7) -- checked against the
live constants they describe.

Deliberately NOT a generic README-vs-code diff engine: this check cannot
know which side is authoritative when they disagree, so it only ever files
a needs-human finding naming both candidate values. It never edits either
side. Extend CLAIMS below by hand whenever a new doc claim references a
tunable constant.
"""
import re

from base import finding, could_not_run

CHECK_ID = "doc_code_drift"
MODE = "soft"

# Each entry: (doc file, regex capturing the claimed number, module.CONSTANT to compare against)
CLAIMS = [
    ("README.md", r"after (\d+) consecutive failed/empty runs", "heal.FAILURE_THRESHOLD"),
    ("README.md", r"capped at (\d+) calls/run", "verify.MAX_VERIFY_CALLS_PER_RUN"),
    ("README.md", r"seen-items\.json\s+dedupe memory \(pruned after (\d+) days\)", "run.SEEN_ITEMS_MAX_AGE_DAYS"),
    ("README.md", r"rolling ~(\d+)-day window of published items", "run.DIGEST_ITEMS_MAX_AGE_DAYS"),
    ("README.md", r"Items published more than (\d+) days ago are ignored at ingest", "fetch.MAX_ITEM_AGE_DAYS"),
    ("CLAUDE.md", r"capped at (\d+) rows", "page.PRIORITY_CAP"),
]


def _live_value(repo_root, ref):
    import sys
    sys.path.insert(0, str(repo_root / "scripts"))
    module_name, attr = ref.split(".")
    if module_name == "page":
        text = (repo_root / "scripts" / "templates" / "page.html").read_text(encoding="utf-8")
        m = re.search(rf"const {attr}\s*=\s*(\d+)", text)
        return int(m.group(1)) if m else None
    mod = __import__(module_name)
    return getattr(mod, attr, None)


def run(repo_root):
    findings = []
    for doc_file, pattern, ref in CLAIMS:
        doc_path = repo_root / doc_file
        try:
            text = doc_path.read_text(encoding="utf-8")
        except Exception as exc:
            findings.append(finding(CHECK_ID, "warn", f"could not read {doc_file}", str(exc), {}))
            continue
        m = re.search(pattern, text)
        if not m:
            continue  # claim's wording changed; not this check's job to guess a new pattern
        claimed = int(m.group(1))
        try:
            live = _live_value(repo_root, ref)
        except Exception as exc:
            findings.append(finding(CHECK_ID, "warn", f"could not read live value for {ref}", str(exc), {}))
            continue
        if live is not None and live != claimed:
            findings.append(finding(
                CHECK_ID, "warn",
                f"{doc_file} claims {claimed}, but {ref} is {live}",
                f"{doc_file} says '{m.group(0)}' but the live constant {ref} = {live}. "
                "This check cannot tell which side is intended -- file this as needs-human and let a "
                "reviewed PR correct whichever one is stale.",
                {"doc": doc_file, "claim": m.group(0), "claimed_value": claimed, "ref": ref, "live_value": live},
            ))
    return findings
