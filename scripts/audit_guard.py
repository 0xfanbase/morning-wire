"""CI guard for .github/workflows/audit-guard.yml.

Runs only against pull requests. Blocks a PR that would defang the
integrity-audit safety net WITHOUT necessarily tripping any single obvious
signal -- e.g. deleting a protected check's file, flipping its MODE from
"hard" to "soft" while leaving PROTECTED_CHECK_IDS untouched, or hand-editing
the two paths (data/registers/, data/seen-items.json) that must only ever be
written by the pipeline itself. This exists because scripts/audit.py's own
"missing protected check" guard only catches a module *disappearing* -- it
can't catch its own detection logic being quietly narrowed in the same PR
that also removes the file, or narrowed on its own with the file kept but
neutered.

Exit 0 = clean. Exit 1 = blocked; prints exactly what and why.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_PATH_PREFIXES = ("data/registers/", "data/seen-items.json")


def _git(args):
    return subprocess.run(["git"] + args, cwd=str(REPO_ROOT), capture_output=True, text=True, check=True).stdout


def _protected_ids_at(ref):
    try:
        text = _git(["show", f"{ref}:scripts/audit.py"])
    except subprocess.CalledProcessError:
        return None
    match = re.search(r"PROTECTED_CHECK_IDS\s*=\s*\{([^}]*)\}", text)
    if not match:
        return None
    return {m.strip().strip('"\'') for m in match.group(1).split(",") if m.strip()}


def _hard_check_ids_at(ref):
    """CHECK_ID -> MODE for every scripts/audit_checks/check_*.py at `ref`."""
    try:
        listing = _git(["ls-tree", "-r", "--name-only", ref, "--", "scripts/audit_checks/"])
    except subprocess.CalledProcessError:
        return {}
    modes = {}
    for path in listing.strip().splitlines():
        if not re.match(r"scripts/audit_checks/check_[^/]+\.py$", path):
            continue
        try:
            text = _git(["show", f"{ref}:{path}"])
        except subprocess.CalledProcessError:
            continue
        id_match = re.search(r'CHECK_ID\s*=\s*["\']([^"\']+)["\']', text)
        mode_match = re.search(r'MODE\s*=\s*["\']([^"\']+)["\']', text)
        if id_match:
            modes[id_match.group(1)] = mode_match.group(1) if mode_match else "soft"
    return modes


def main():
    base_ref = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    head_ref = sys.argv[2] if len(sys.argv) > 2 else "HEAD"

    problems = []

    base_protected = _protected_ids_at(base_ref) or set()
    head_protected = _protected_ids_at(head_ref)
    if head_protected is None:
        problems.append("scripts/audit.py is missing or unparseable at the PR head -- PROTECTED_CHECK_IDS could not be verified.")
        head_protected = set()

    shrunk = base_protected - head_protected
    if shrunk:
        problems.append(f"PROTECTED_CHECK_IDS shrank: {sorted(shrunk)} removed from scripts/audit.py.")

    head_modes = _hard_check_ids_at(head_ref)
    for check_id in head_protected:
        mode = head_modes.get(check_id)
        if mode is None:
            problems.append(f"Protected check '{check_id}' has no corresponding check_*.py module at the PR head.")
        elif mode != "hard":
            problems.append(f"Protected check '{check_id}' was downgraded to MODE={mode!r} (must stay 'hard').")

    try:
        diff_files = _git(["diff", "--name-only", f"{base_ref}...{head_ref}"]).strip().splitlines()
    except subprocess.CalledProcessError as exc:
        problems.append(f"could not diff {base_ref}...{head_ref}: {exc}")
        diff_files = []

    forbidden_touched = [f for f in diff_files if f.startswith(FORBIDDEN_PATH_PREFIXES)]
    if forbidden_touched:
        problems.append(
            "PR touches pipeline-memory paths that must never be hand-edited via PR "
            f"(only the daily pipeline's own commit-back may write them): {forbidden_touched}"
        )

    if problems:
        print("audit-guard: BLOCKED")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    print("audit-guard: clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
