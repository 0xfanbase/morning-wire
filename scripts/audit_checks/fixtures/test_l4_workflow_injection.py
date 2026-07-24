"""Red/green fixture for audit/lessons.md L4: proves check_workflow_injection
actually fires on the exact vulnerable pattern found live in
.github/workflows/audit-guard.yml (an untrusted `${{ github.head_ref }}`
interpolated straight into a `run:` shell line), and stays clean once the
value is moved through `env:` instead.

This is a proactively-found vulnerability, not a historical incident (like
L2's fixture, not L1's) -- so it builds synthetic workflow YAML in a scratch
directory rather than replaying real repo history.

Run manually: python3 scripts/audit_checks/fixtures/test_l4_workflow_injection.py
Exits non-zero if any assertion fails.
"""
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

VULNERABLE_WORKFLOW = """\
name: Audit guard
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: Check protected core + forbidden paths
        run: python scripts/audit_guard.py "origin/${{ github.event.pull_request.base.ref }}" "${{ github.event.pull_request.head.sha }}" "${{ github.event.pull_request.user.login }}" "${{ github.head_ref }}"
"""

FIXED_WORKFLOW = """\
name: Audit guard
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  guard:
    runs-on: ubuntu-latest
    steps:
      - name: Check protected core + forbidden paths
        env:
          PR_BASE_REF: ${{ github.event.pull_request.base.ref }}
          PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
          PR_ACTOR: ${{ github.event.pull_request.user.login }}
          PR_HEAD_REF: ${{ github.head_ref }}
        run: python scripts/audit_guard.py "origin/$PR_BASE_REF" "$PR_HEAD_SHA" "$PR_ACTOR" "$PR_HEAD_REF"
"""

# The sequence-item shorthand (`- run:` as the step's first key) is exactly as
# executable as the `- name:` + `run:` mapping form -- a check that only
# matches `run:` at the start of a line (after whitespace) scans this form
# clean and misses the identical injection.
VULNERABLE_LIST_FORM = """\
name: List-form step
on: [pull_request]
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.head_ref }}"
      - run: |
          echo "${{ github.event.pull_request.title }}"
"""

# Same list-form steps with the untrusted values moved through env: -- the
# sibling env: sits at the column of the `run` token (deeper than the `-`),
# so the block-scalar continuation scan must stop before it rather than
# swallow it into the scanned shell text and false-positive on it.
FIXED_LIST_FORM = """\
name: List-form step
on: [pull_request]
jobs:
  j:
    runs-on: ubuntu-latest
    steps:
      - run: echo "$HEAD_REF"
        env:
          HEAD_REF: ${{ github.head_ref }}
      - run: |
          echo "$PR_TITLE"
        env:
          PR_TITLE: ${{ github.event.pull_request.title }}
"""


def _write_workflows(tmp, content, filename="audit-guard.yml"):
    wf_dir = Path(tmp) / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / filename).write_text(content, encoding="utf-8")
    return Path(tmp)


def main():
    failures = []
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    import check_workflow_injection as check

    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflows(tmp, VULNERABLE_WORKFLOW)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        head_ref_flagged = any(f["evidence"].get("expression") == "github.head_ref" for f in criticals)
        print(f"RED case (unfixed audit-guard.yml pattern): {len(criticals)} critical finding(s)")
        if not head_ref_flagged:
            failures.append("RED FIXTURE FAILED: check_workflow_injection did not flag github.head_ref "
                             "interpolated directly into a run: block")
        false_positives = [f for f in criticals if f["evidence"].get("expression") != "github.head_ref"]
        if false_positives:
            failures.append(f"RED FIXTURE FAILED: unexpected extra finding(s): {false_positives}")

    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflows(tmp, FIXED_WORKFLOW)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        print(f"GREEN case (env: pattern): {len(criticals)} critical finding(s)")
        if criticals:
            failures.append(f"GREEN FIXTURE FAILED: check_workflow_injection still fires once the "
                             f"value is passed through env: -- {criticals}")

    # RED: `- run:` sequence-item shorthand, single-line AND block-scalar.
    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflows(tmp, VULNERABLE_LIST_FORM)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        exprs = {f["evidence"].get("expression") for f in criticals}
        print(f"RED case (list-form `- run:`): {len(criticals)} critical finding(s)")
        if "github.head_ref" not in exprs:
            failures.append("RED FIXTURE FAILED: single-line `- run: ${{ github.head_ref }}` "
                             "(sequence-item shorthand) was not flagged")
        if "github.event.pull_request.title" not in exprs:
            failures.append("RED FIXTURE FAILED: block-scalar `- run: |` under a sequence item "
                             "was not flagged")

    # GREEN: same list-form steps fixed via sibling env: -- must be clean,
    # proving the continuation-indent threshold (column of `run`, not of `-`)
    # keeps the env: block out of the scanned shell text.
    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflows(tmp, FIXED_LIST_FORM)
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        print(f"GREEN case (list-form + env:): {len(criticals)} critical finding(s)")
        if criticals:
            failures.append(f"GREEN FIXTURE FAILED: list-form env: sibling was swallowed into the "
                             f"run: block -- {criticals}")

    # RED: identical vulnerable content in a .yaml file -- GitHub Actions
    # loads both extensions, so the scan must too.
    with tempfile.TemporaryDirectory() as tmp:
        root = _write_workflows(tmp, VULNERABLE_WORKFLOW, filename="audit-guard.yaml")
        findings = check.run(root)
        criticals = [f for f in findings if f["severity"] == "critical"]
        print(f"RED case (.yaml extension): {len(criticals)} critical finding(s)")
        if not any(f["evidence"].get("expression") == "github.head_ref" for f in criticals):
            failures.append("RED FIXTURE FAILED: a vulnerable workflow named *.yaml was not scanned")

    # Also confirm this repo's OWN current, real workflow files are clean --
    # this is what actually protects future PRs, not just the synthetic case.
    findings = check.run(REPO_ROOT)
    criticals = [f for f in findings if f["severity"] == "critical"]
    print(f"Live repo .github/workflows/*.yml: {len(criticals)} critical finding(s)")
    if criticals:
        failures.append(f"LIVE REPO CHECK FAILED: real workflow files still have a script-injection "
                         f"pattern: {criticals}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
