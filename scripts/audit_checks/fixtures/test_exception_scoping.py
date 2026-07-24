"""Red/green fixture for audit/exceptions.json scoping in scripts/audit.py.

The PLAYBOOK's never-list requires every suppression to target ONE exact
finding (check id + evidence keys) and never a whole check. Two holes this
fixture pins shut:

  RED 1: an entry with an EMPTY evidence dict matched every finding of its
       check -- `all()` over zero keys is vacuously true -- i.e. a wildcard
       whole-check suppression the playbook forbids. _load_active_exceptions
       must refuse such an entry.
  RED 2: the PROTECTED-check suppression bypass only excluded
       severity == "critical", so a could_not_run on a PROTECTED check was
       suppressible -- but "a guard that cannot run must be as loud as a
       guard that fails" (PLAYBOOK Phase 1), and audit.py's own exit-code
       gate treats could_not_run as a hard failure. The bypass must cover
       both severities.
  GREEN: a correctly-scoped, unexpired entry for a soft check's finding
       still suppresses exactly that finding; an expired one does not.

Run manually: python3 scripts/audit_checks/fixtures/test_exception_scoping.py
Exits non-zero if any assertion fails.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _write_exceptions(tmp, entries):
    root = Path(tmp)
    (root / "audit").mkdir()
    (root / "audit" / "exceptions.json").write_text(json.dumps(entries), encoding="utf-8")
    return root


def main():
    failures = []
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import audit

    protected_id = sorted(audit.PROTECTED_CHECK_IDS)[0]

    # RED 1: empty evidence == whole-check wildcard -- must not load.
    with tempfile.TemporaryDirectory() as tmp:
        root = _write_exceptions(tmp, [
            {"check": "source_health", "evidence": {}, "expires": "2999-01-01", "reason": "wildcard"},
        ])
        active = audit._load_active_exceptions(root, today="2026-07-24")
        print(f"RED 1 (empty-evidence entry): {len(active)} active exception(s)")
        if active:
            failures.append("RED FIXTURE FAILED: an empty-evidence exceptions.json entry was accepted; "
                             "it would suppress EVERY finding of that check (vacuous all())")

    # RED 2: a PROTECTED check's could_not_run must be unsuppressible even
    # when an entry matches its evidence exactly.
    findings = [
        {"check": protected_id, "severity": "could_not_run",
         "title": f"{protected_id} could not run", "detail": "x",
         "evidence": {"bootstrap_expected": True}, "mode": "hard"},
        {"check": protected_id, "severity": "critical",
         "title": "backdate", "detail": "x", "evidence": {"id": "item-1"}, "mode": "hard"},
    ]
    exceptions = [
        {"check": protected_id, "evidence": {"bootstrap_expected": True},
         "expires": "2999-01-01", "reason": "trying to silence a protected guard"},
        {"check": protected_id, "evidence": {"id": "item-1"},
         "expires": "2999-01-01", "reason": "trying to silence a protected critical"},
    ]
    audit._apply_exceptions(findings, exceptions)
    print(f"RED 2 (protected bypass): suppressed flags = {[f['suppressed'] for f in findings]}")
    if findings[0]["suppressed"]:
        failures.append("RED FIXTURE FAILED: a PROTECTED check's could_not_run was suppressed via "
                         "exceptions.json -- the bypass must cover could_not_run, not just critical")
    if findings[1]["suppressed"]:
        failures.append("RED FIXTURE FAILED: a PROTECTED check's critical was suppressed (existing "
                         "invariant regressed)")

    # GREEN: exact-scoped suppression of a soft check's finding still works,
    # and an expired entry does not load.
    with tempfile.TemporaryDirectory() as tmp:
        root = _write_exceptions(tmp, [
            {"check": "source_health", "evidence": {"name": "Example Wire"},
             "expires": "2999-01-01", "reason": "known flaky; human-acked"},
            {"check": "source_health", "evidence": {"name": "Old Wire"},
             "expires": "2020-01-01", "reason": "expired long ago"},
        ])
        active = audit._load_active_exceptions(root, today="2026-07-24")
        soft_findings = [
            {"check": "source_health", "severity": "warn", "title": "flaky", "detail": "x",
             "evidence": {"name": "Example Wire"}, "mode": "soft"},
            {"check": "source_health", "severity": "warn", "title": "other", "detail": "x",
             "evidence": {"name": "Other Wire"}, "mode": "soft"},
            {"check": "source_health", "severity": "warn", "title": "expired", "detail": "x",
             "evidence": {"name": "Old Wire"}, "mode": "soft"},
        ]
        audit._apply_exceptions(soft_findings, active)
        print(f"GREEN (scoped soft suppression): suppressed flags = {[f['suppressed'] for f in soft_findings]}")
        if not soft_findings[0]["suppressed"]:
            failures.append("GREEN FIXTURE FAILED: a correctly-scoped, unexpired suppression of a soft "
                             "finding no longer applies")
        if soft_findings[1]["suppressed"]:
            failures.append("GREEN FIXTURE FAILED: a suppression leaked onto a finding whose evidence "
                             "does not match")
        if soft_findings[2]["suppressed"]:
            failures.append("GREEN FIXTURE FAILED: an expired exception entry was applied")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
