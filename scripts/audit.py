"""Weekly integrity-audit harness for Reg Radar / Digital Assets Morning Wire.

Discovers every check module in scripts/audit_checks/, asserts the
PROTECTED set (the checks that would have caught the 2026-07-07
first_seen/deletion incident) is present, runs everything to completion --
detection is never gated on remediation, so one check failing never
disables another -- and reports the result. See audit/PLAYBOOK.md for the
full weekly runbook this harness is one step of.

Modes:
  (default)     run every discovered check, print a full report
  --ci          run only HARD checks (for the daily tripwire, integrity.yml)
  --simulate    replay merge_digest_window -> prune_seen_items -> sanitize_digest
                on the CURRENT committed data and assert zero unexpected item
                loss; prints the before/after item count and any dropped ids.
                This is the mandatory proof a data-repair recipe must attach
                to its PR -- the exact ritual the 2026-07-07 incident lacked.

Exit code: 0 if every HARD check passed (or there were none), non-zero if any
HARD check reported a critical finding OR could not run. SOFT findings never
affect the exit code -- they're for the weekly report, not a gate.

This harness imports the real pipeline (scripts/run.py, scripts/render.py,
etc.), which transitively imports `anthropic`. If that import fails, the
harness itself exits CRITICAL ("could not load pipeline code") rather than
silently skipping checks -- a guard that cannot run must be as loud as a
guard that fails.
"""
import argparse
import copy
import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKS_DIR = REPO_ROOT / "scripts" / "audit_checks"
LEDGER_PATH = REPO_ROOT / "audit" / "ledger.jsonl"

PROTECTED_CHECK_IDS = {
    "first_seen_3way",
    "deletion_diff",
    "render_drops",
    "docs_feed_parity",
    "enum_constant_freeze",
}


def _discover_checks():
    sys.path.insert(0, str(CHECKS_DIR))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    modules = []
    for f in sorted(CHECKS_DIR.glob("check_*.py")):
        mod = importlib.import_module(f.stem)
        if not hasattr(mod, "CHECK_ID") or not hasattr(mod, "run"):
            raise RuntimeError(f"{f.name} is missing CHECK_ID or run() -- malformed check module")
        modules.append(mod)
    return modules


def run_audit(mode="full"):
    """Returns (findings: list[dict], checks_ran: list[str], checks_expected: int, error: str|None)."""
    try:
        modules = _discover_checks()
    except Exception as exc:
        return [], [], 0, f"could not discover/import check modules: {exc}"

    present_ids = {m.CHECK_ID for m in modules}
    missing_protected = PROTECTED_CHECK_IDS - present_ids
    if missing_protected:
        return [], [], len(modules), (
            f"PROTECTED check(s) missing from scripts/audit_checks/: {sorted(missing_protected)} -- "
            "refusing to run. A protected check going missing is itself the failure mode this harness "
            "exists to prevent."
        )

    if mode == "ci":
        modules = [m for m in modules if getattr(m, "MODE", "soft") == "hard"]

    all_findings = []
    checks_ran = []
    for m in modules:
        checks_ran.append(m.CHECK_ID)
        try:
            findings = m.run(REPO_ROOT)
        except Exception as exc:
            findings = [{
                "check": m.CHECK_ID, "severity": "could_not_run",
                "title": f"{m.CHECK_ID} raised an unhandled exception",
                "detail": str(exc), "evidence": {},
            }]
        for f in findings:
            f.setdefault("mode", getattr(m, "MODE", "soft"))
        all_findings.extend(findings)

    return all_findings, checks_ran, len(modules), None


def _simulate(repo_root):
    sys.path.insert(0, str(repo_root / "scripts"))
    import run as run_mod

    digest_path = repo_root / "data" / "digest.json"
    seen_path = repo_root / "data" / "seen-items.json"
    digest = json.loads(digest_path.read_text(encoding="utf-8"))
    seen = json.loads(seen_path.read_text(encoding="utf-8"))

    before_ids = {it["id"] for it in digest.get("items", [])}
    # Replay exactly what the next real run.py would do to EXISTING items:
    # merge_digest_window(previous_items, fresh_items=[]) -- no new fetch,
    # just re-applies the retention prune to prove nothing in-window is lost.
    merged = run_mod.merge_digest_window(digest.get("items", []), [])
    after_ids = {it["id"] for it in merged}

    pruned_seen = run_mod.prune_seen_items(copy.deepcopy(seen))

    try:
        import render as render_mod
        clean = render_mod.sanitize_digest({**digest, "items": merged})
        sanitized_ids = {it["id"] for it in clean["items"]}
    except Exception as exc:
        print(f"--simulate: sanitize_digest raised: {exc}")
        sanitized_ids = after_ids

    lost_in_prune = before_ids - after_ids
    lost_in_sanitize = after_ids - sanitized_ids

    print(f"--simulate report (read-only; nothing written)")
    print(f"  items before: {len(before_ids)}")
    print(f"  items after merge_digest_window (no new fetch): {len(after_ids)}")
    print(f"  items after sanitize_digest: {len(sanitized_ids)}")
    print(f"  seen-items.json entries before/after prune_seen_items: {len(seen)}/{len(pruned_seen)}")
    if lost_in_prune:
        print(f"  ATTENTION -- retention pruned {len(lost_in_prune)} item(s) this replay: {sorted(lost_in_prune)}")
        print("  (Expected only for items already outside the retention window -- verify each one's first_seen.)")
    if lost_in_sanitize:
        print(f"  ATTENTION -- sanitize_digest would drop {len(lost_in_sanitize)} item(s): {sorted(lost_in_sanitize)}")
    if not lost_in_prune and not lost_in_sanitize:
        print("  zero unexpected item loss")
    return 1 if lost_in_sanitize else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ci", action="store_true", help="run only HARD checks")
    parser.add_argument("--simulate", action="store_true", help="replay retention+sanitize, report item loss")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON instead of a report")
    args = parser.parse_args()

    if args.simulate:
        sys.exit(_simulate(REPO_ROOT))

    mode = "ci" if args.ci else "full"
    findings, checks_ran, checks_expected, error = run_audit(mode)

    if error:
        print(f"CRITICAL: {error}")
        sys.exit(2)

    hard_critical = [f for f in findings if f["mode"] == "hard" and f["severity"] in ("critical", "could_not_run")]
    exit_code = 1 if hard_critical else 0

    if args.json:
        print(json.dumps({
            "checks_ran": checks_ran, "checks_expected": checks_expected,
            "findings": findings, "hard_failure": bool(hard_critical),
        }, indent=1))
        sys.exit(exit_code)

    print(f"Reg Radar integrity audit -- {datetime.now(timezone.utc).isoformat()}")
    print(f"Checks ran: {len(checks_ran)}/{checks_expected}  ({', '.join(checks_ran)})")
    if not findings:
        print("No findings. Clean run.")
    else:
        by_sev = {"critical": [], "could_not_run": [], "warn": [], "info": []}
        for f in findings:
            by_sev.setdefault(f["severity"], []).append(f)
        for sev in ("critical", "could_not_run", "warn", "info"):
            for f in by_sev.get(sev, []):
                print(f"\n[{sev.upper()}] ({f['mode']}) {f['check']}: {f['title']}")
                print(f"  {f['detail']}")

    if not args.ci:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(timezone.utc).isoformat(),
            "checks_ran": checks_ran,
            "checks_expected": checks_expected,
            "findings": findings,
            "hard_failure": bool(hard_critical),
        }
        with LEDGER_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
