"""SOFT but treat findings as urgent. Two independent scans over the
CURRENT working tree (a full git-history secret scan was done once by hand
this session and should be repeated manually if ever in doubt -- this check
covers ongoing drift, not history):

1. Secret-pattern scan over every tracked text file -- API keys, private key
   headers, generic token= assignments. A hit here means something leaked
   into a commit that shouldn't have.
2. Neutrality scan over the published digest content (title/summary/so_what)
   for institutional first-person voice ("our bank", "we recommend", "your
   firm's") -- the page's whole design promise is neutrality; an enrichment
   session accidentally writing as an insider is a real regression risk this
   catches without hardcoding any specific person's name into a test file
   (which would itself be a neutrality/PII problem).
"""
import json
import re
import subprocess

from base import finding, could_not_run

CHECK_ID = "neutrality_secrets"
MODE = "soft"

SECRET_PATTERNS = [
    r"sk-ant-[a-zA-Z0-9_-]{20,}",
    r"sk-[a-zA-Z0-9]{32,}",
    r"ghp_[A-Za-z0-9]{30,}",
    r"github_pat_[A-Za-z0-9_]{30,}",
    r"gho_[A-Za-z0-9]{30,}",
    r"AKIA[0-9A-Z]{16}",
    r"xox[bpars]-[A-Za-z0-9-]{10,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"AIza[0-9A-Za-z_-]{35}",
]

NEUTRALITY_PATTERNS = [
    r"\bour bank\b", r"\bour firm\b", r"\bour institution\b",
    r"\bwe recommend\b", r"\bwe advise\b", r"\bwe believe\b",
    r"\byour firm'?s\b", r"\byour bank'?s\b", r"\bmy employer\b",
]


def run(repo_root):
    findings = []

    try:
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=str(repo_root), capture_output=True, text=True, check=True, timeout=15,
        ).stdout.splitlines()
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not list tracked files: {exc}")]

    secret_re = re.compile("|".join(SECRET_PATTERNS))
    for rel in tracked:
        path = repo_root / rel
        if not path.is_file() or path.suffix in (".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        m = secret_re.search(text)
        if m:
            findings.append(finding(
                CHECK_ID, "critical", f"possible secret pattern in {rel}",
                f"Matched a credential-shaped pattern near position {m.start()}. Rotate the credential "
                "immediately if real, then scrub history (this is a working-tree scan, not history).",
                {"file": rel},
            ))

    try:
        digest = json.loads((repo_root / "data" / "digest.json").read_text(encoding="utf-8"))
    except Exception as exc:
        findings.append(could_not_run(CHECK_ID + ":neutrality", f"could not read data/digest.json: {exc}"))
        return findings

    neutrality_re = re.compile("|".join(NEUTRALITY_PATTERNS), re.IGNORECASE)
    for it in digest.get("items", []):
        for field in ("title", "summary", "so_what"):
            text = str(it.get(field, ""))
            m = neutrality_re.search(text)
            if m:
                findings.append(finding(
                    CHECK_ID, "warn", f"institutional first-person voice in item {it.get('id')}.{field}",
                    f"Matched {m.group(0)!r} in: {text[:120]}. The page's neutrality rule (CLAUDE.md) "
                    "requires neutral voice -- never 'we'/'our bank'/'your firm'.",
                    {"id": it.get("id"), "field": field, "matched": m.group(0)},
                ))
    return findings
