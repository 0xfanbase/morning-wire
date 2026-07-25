"""Red/green fixture for audit/lessons.md L9: proves check_workflow_injection
catches an untrusted GitHub-context expression written in the common
single-line `- run: cmd` inline-list-item form, which its original
_run_blocks implementation (added the same session, in L4) missed entirely
-- found by an adversarial red-team pass explicitly trying to disprove the
check's own "clean" verdict rather than confirm it.

Run manually: python3 scripts/audit_checks/fixtures/test_l6_inline_dash_evasion.py
Exits non-zero if any assertion fails.
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

INLINE_DASH_VULNERABLE = """\
name: Example
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.head_ref }}"
"""

INLINE_DASH_FIXED = """\
name: Example
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - env:
          HEAD_REF: ${{ github.head_ref }}
        run: echo "$HEAD_REF"
"""

BLOCK_SCALAR_INLINE_DASH_VULNERABLE = """\
name: Example
on: [pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: |
          echo "starting"
          echo "${{ github.head_ref }}"
      - name: unrelated step
        run: echo "done"
"""


def _write_workflow(tmp, content):
    wf_dir = Path(tmp) / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "example.yml").write_text(content, encoding="utf-8")
    return Path(tmp)


def main():
    failures = []
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    import check_workflow_injection as check

    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflow(tmp, INLINE_DASH_VULNERABLE)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        head_ref_flagged = any(f["evidence"].get("expression") == "github.head_ref" for f in criticals)
        print(f"RED case (inline-dash '- run:' form): {len(criticals)} critical finding(s)")
        if not head_ref_flagged:
            failures.append("RED FIXTURE FAILED: check_workflow_injection did not flag github.head_ref "
                             "written as '- run: cmd' (inline sequence-item form)")

    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflow(tmp, BLOCK_SCALAR_INLINE_DASH_VULNERABLE)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        head_ref_flagged = any(f["evidence"].get("expression") == "github.head_ref" for f in criticals)
        print(f"RED case (inline-dash '- run: |' block scalar): {len(criticals)} critical finding(s)")
        if not head_ref_flagged:
            failures.append("RED FIXTURE FAILED: check_workflow_injection did not flag github.head_ref "
                             "inside a '- run: |' block-scalar body")
        if len(criticals) != 1:
            failures.append(f"RED FIXTURE FAILED: expected exactly 1 finding (the unrelated second step "
                             f"must not be flagged), got {len(criticals)}: {criticals}")

    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflow(tmp, INLINE_DASH_FIXED)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        print(f"GREEN case (env: pattern, inline-dash step): {len(criticals)} critical finding(s)")
        if criticals:
            failures.append(f"GREEN FIXTURE FAILED: still fires once the value is passed through "
                             f"env: -- {criticals}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
