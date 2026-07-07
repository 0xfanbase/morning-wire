"""SOFT. data/registers/ snapshot filenames are derived from
registers._slug(source['name']) -- so renaming a register source's display
name (as happened when sources.json was de-acronym'd) orphans the OLD
snapshot file on disk forever; it is simply never read or written again.

Propose-only: data/registers/ is pipeline memory (CLAUDE.md forbids
hand-editing it), so this check only ever recommends deletion in the PR
body -- it never deletes the file itself.
"""
import json

from base import finding, could_not_run

CHECK_ID = "orphan_registers"
MODE = "soft"


def run(repo_root):
    try:
        import sys
        sys.path.insert(0, str(repo_root / "scripts"))
        import registers as registers_mod
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not import scripts/registers.py: {exc}")]

    try:
        sources = json.loads((repo_root / "data" / "sources.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return [could_not_run(CHECK_ID, f"could not read data/sources.json: {exc}")]

    expected_slugs = {registers_mod._slug(s["name"]) + ".json"
                      for s in sources if s.get("kind") == "register"}

    reg_dir = repo_root / "data" / "registers"
    if not reg_dir.exists():
        return []

    findings = []
    for f in sorted(reg_dir.glob("*.json")):
        if f.name not in expected_slugs:
            findings.append(finding(
                CHECK_ID, "info",
                f"orphaned register snapshot: data/registers/{f.name}",
                "This file does not match any current register source's slug -- it was likely left "
                "behind by a source rename and is never read or written by the live pipeline. "
                "PROPOSE deletion in the PR; do not delete it directly (data/registers/ is pipeline memory).",
                {"file": f.name, "expected_slugs": sorted(expected_slugs)},
            ))
    return findings
