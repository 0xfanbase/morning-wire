"""Red/green fixture for audit/lessons.md L9: proves check_neutrality_secrets
now catches four secret shapes an adversarial red-team pass found missing
from its original pattern list -- a GitHub App installation/user/refresh
token (ghs_/ghu_/ghr_), a Slack incoming-webhook URL, a basic-auth-style
connection string, and an unquoted .env-style KEY=value assignment (the
original generic rule required a quoted value) -- while confirming the
green (benign-text) case still stays clean.

Run manually: python3 scripts/audit_checks/fixtures/test_l6_secret_pattern_gaps.py
Exits non-zero if any assertion fails.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

RED_CASES = {
    "ghs_ token": "ghs_" + "a" * 36,
    "ghu_ token": "ghu_" + "b" * 36,
    "ghr_ token": "ghr_" + "c" * 36,
    # Split across a concatenation (not one contiguous literal) so this
    # synthetic test fixture doesn't itself look enough like a real Slack
    # webhook to trip GitHub's own push-protection secret scanner.
    "Slack incoming webhook": "https://hooks.slack.com/services/" + "T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX",
    "DB connection string": "postgresql://admin:sup3rSecretPW9@db.example.com:5432/mydb",
    "unquoted .env-style key": "API_KEY=abcdefgh12345678ijklmnop",
}

GREEN_CASES = {
    "prose mentioning api_key with no assignment": "See the docs for how api_key relates to your account.",
    "bare user@host, no password": "Contact admin@example.com for access.",
}


def main():
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    import check_neutrality_secrets as check

    secret_re = re.compile("|".join(check.SECRET_PATTERNS))
    failures = []

    for name, text in RED_CASES.items():
        m = secret_re.search(text)
        print(f"RED case ({name}): {'MATCHED' if m else 'no match'}")
        if not m:
            failures.append(f"RED FIXTURE FAILED: '{name}' was not detected: {text!r}")

    for name, text in GREEN_CASES.items():
        m = secret_re.search(text)
        print(f"GREEN case ({name}): {'MATCHED (unexpected)' if m else 'no match'}")
        if m:
            failures.append(f"GREEN FIXTURE FAILED: '{name}' incorrectly matched: {m.group(0)!r}")

    # Also confirm the live repo itself has zero findings -- this file's own
    # explanatory prose must not accidentally look like a real secret (this
    # bit the first version of the DB-connection-string pattern's comment).
    findings = check.run(REPO_ROOT)
    critical = [f for f in findings if f["severity"] == "critical"]
    print(f"Live repo: {len(critical)} critical finding(s)")
    if critical:
        failures.append(f"LIVE REPO CHECK FAILED: {critical}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
