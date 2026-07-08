"""Red/green fixture for audit/lessons.md L2: proves the two hardenings to
check_first_seen_3way / check_deletion_diff actually close the mutable-
dedupe-key evasion gap a 4-round Fable audit found (2026-07-08).

Unlike test_against_incident.py (built from this repo's own real cd206e3
incident), L2 is a proactively-found vulnerability, not a historical one --
there is no real bad commit in this repo's history to replay. So this
fixture builds a minimal synthetic git repo from scratch, reproducing the
exact exploit shape described in L2:

  commit 1 (base):   item first appears, id="test-item-001",
                      first_seen=2026-01-10.
  commit 2 (attack):  SAME id, url edited (so the OLD dedupe-key-based
                      anchor would see this as a brand-new key with no
                      history) AND first_seen backdated hard, to 2025-01-01.
  commit 3 (delete):  the item is removed entirely. generated_at is only
                      2 days after commit 2 -- well inside the real
                      retention window -- but the item's OWN recorded
                      first_seen (as of commit 2) is over a year in the
                      past, which is what a check trusting only "the
                      immediately preceding commit's value" would use to
                      (wrongly) call this a legal age-out.

A green companion (test_url_edit_alone) proves a legitimate, non-backdated
url fix is NOT mistaken for a deletion by the new id-based matching.

Run manually: python3 scripts/audit_checks/fixtures/test_l2_mutable_key_evasion.py
Exits non-zero if any assertion fails.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _git(args, cwd):
    return subprocess.run(["git"] + args, cwd=str(cwd), capture_output=True, text=True, check=True, timeout=30).stdout


def _init_repo(tmp):
    repo = Path(tmp)
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Fixture"], repo)
    (repo / "data").mkdir()
    return repo


def _write_digest(repo, generated_at, items):
    (repo / "data" / "digest.json").write_text(
        json.dumps({"generated_at": generated_at, "items": items, "source_health": [], "run_log": []}, indent=2),
        encoding="utf-8",
    )


def _commit(repo, message):
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", message], repo)


def _base_item(item_id, url, first_seen, jurisdiction="GLOBAL", source="Test Source"):
    return {
        "id": item_id, "jurisdiction": jurisdiction, "source": source,
        "title": "Test item", "url": url, "published": first_seen, "type": "news",
        "priority": "normal", "status": "new",
        "verification": {"level": "single_source", "sources": [{"name": source, "url": url}]},
        "summary": "Test item", "so_what": "Test item", "first_seen": first_seen,
    }


def test_backdate_then_delete_evasion():
    """The attack scenario: url edit + backdate, then delete. Must be caught."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(tmp)
        (repo / "data" / "seen-items.json").write_text("{}", encoding="utf-8")

        item = _base_item("test-item-001", "https://example.com/original", "2026-01-10T00:00:00+00:00")
        _write_digest(repo, "2026-01-10T00:00:00+00:00", [item])
        _commit(repo, "commit 1: base")

        attacked = _base_item("test-item-001", "https://example.com/original-relinked", "2025-01-01T00:00:00+00:00")
        _write_digest(repo, "2026-01-11T00:00:00+00:00", [attacked])
        _commit(repo, "commit 2: url edit + backdate")

        sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import importlib
        import check_first_seen_3way as c1
        importlib.reload(c1)

        findings = c1.run(repo, bootstrap_cutoff=None)
        criticals = [f for f in findings if f["severity"] == "critical"]
        assert criticals, (
            "check_first_seen_3way did NOT catch the url-edit+backdate at commit 2 -- "
            f"the id-anchor hardening is not working. All findings: {findings}"
        )

        _write_digest(repo, "2026-01-12T00:00:00+00:00", [])  # commit 3: deletion
        _commit(repo, "commit 3: delete")

        import check_deletion_diff as c2
        importlib.reload(c2)
        findings_del = c2.run(repo, bootstrap_cutoff=None)
        criticals_del = [f for f in findings_del if f["severity"] == "critical"
                         and f.get("evidence", {}).get("id") == "test-item-001"]
        assert criticals_del, (
            "check_deletion_diff did NOT flag the deletion as illegal -- it trusted the "
            f"already-backdated first_seen instead of the full-history id anchor. All findings: {findings_del}"
        )
        print("test_backdate_then_delete_evasion: PASS "
              f"({len(criticals)} first_seen_3way critical, {len(criticals_del)} deletion_diff critical, as expected)")


def test_url_edit_alone_is_not_a_false_deletion():
    """Green case: a legitimate url fix (no backdating) must NOT be reported
    as a deletion by the new id-based matching."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(tmp)
        (repo / "data" / "seen-items.json").write_text("{}", encoding="utf-8")

        item = _base_item("test-item-002", "https://example.com/typo-url", "2026-01-10T00:00:00+00:00")
        _write_digest(repo, "2026-01-10T00:00:00+00:00", [item])
        _commit(repo, "commit 1: base")

        fixed = _base_item("test-item-002", "https://example.com/correct-url", "2026-01-10T00:00:00+00:00")
        _write_digest(repo, "2026-01-11T00:00:00+00:00", [fixed])
        _commit(repo, "commit 2: legitimate url fix, no backdating")

        sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import importlib
        import check_deletion_diff as c2
        importlib.reload(c2)

        findings = c2.run(repo, bootstrap_cutoff=None)
        false_deletions = [f for f in findings if f["severity"] in ("critical", "warn")
                           and f.get("evidence", {}).get("id") == "test-item-002"]
        assert not false_deletions, (
            f"A legitimate url edit with no backdating was misreported as a deletion: {false_deletions}"
        )
        info_notes = [f for f in findings if f["severity"] == "info"
                      and f.get("evidence", {}).get("id") == "test-item-002"]
        assert info_notes, "Expected an info-level note that the url changed (not a critical/warn deletion)"
        print("test_url_edit_alone_is_not_a_false_deletion: PASS (no false deletion; info note recorded instead)")


def test_update_status_no_longer_excuses_large_backdate():
    """Gap 5: status='update' used to blanket-exempt ANY backward first_seen
    move. A genuine update only ever moves first_seen forward (see
    check_first_seen_3way's UPDATE_BACKDATE_TOLERANCE comment) -- so marking
    a manipulated item 'update' must no longer be a free pass for a large
    backdate, while a small one (same-run timing noise) still is."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(tmp)
        (repo / "data" / "seen-items.json").write_text("{}", encoding="utf-8")

        item = _base_item("test-item-003", "https://example.com/a", "2026-01-10T00:00:00+00:00")
        _write_digest(repo, "2026-01-10T00:00:00+00:00", [item])
        _commit(repo, "commit 1: base")

        backdated_update = _base_item("test-item-003", "https://example.com/a", "2025-06-01T00:00:00+00:00")
        backdated_update["status"] = "update"
        _write_digest(repo, "2026-01-11T00:00:00+00:00", [backdated_update])
        _commit(repo, "commit 2: large backdate disguised as an update")

        sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import importlib
        import check_first_seen_3way as c1
        importlib.reload(c1)

        findings = c1.run(repo, bootstrap_cutoff=None)
        criticals = [f for f in findings if f["severity"] == "critical" and f.get("evidence", {}).get("id") == "test-item-003"]
        assert criticals, (
            f"A large backdate marked status='update' was NOT flagged -- the blanket exemption gap is still open. Findings: {findings}"
        )
        print("test_update_status_no_longer_excuses_large_backdate: PASS "
              f"({len(criticals)} critical finding(s) despite status='update')")


def test_update_status_small_backdate_still_excused():
    """Green case: a small (same-run-timing-scale) backward move under a
    genuine status='update' must still NOT be flagged -- the tolerance
    exists precisely so ordinary update timing noise isn't a false alarm."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _init_repo(tmp)
        (repo / "data" / "seen-items.json").write_text("{}", encoding="utf-8")

        item = _base_item("test-item-004", "https://example.com/b", "2026-01-10T12:00:00+00:00")
        _write_digest(repo, "2026-01-10T12:00:00+00:00", [item])
        _commit(repo, "commit 1: base")

        small_backdate_update = _base_item("test-item-004", "https://example.com/b", "2026-01-10T08:00:00+00:00")
        small_backdate_update["status"] = "update"
        _write_digest(repo, "2026-01-11T00:00:00+00:00", [small_backdate_update])
        _commit(repo, "commit 2: 4-hour backward move, genuine update")

        sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        import importlib
        import check_first_seen_3way as c1
        importlib.reload(c1)

        findings = c1.run(repo, bootstrap_cutoff=None)
        criticals = [f for f in findings if f["severity"] == "critical" and f.get("evidence", {}).get("id") == "test-item-004"]
        assert not criticals, f"A small, tolerance-scale backward move under status='update' was wrongly flagged: {criticals}"
        print("test_update_status_small_backdate_still_excused: PASS (small move correctly excused)")


def main():
    failures = []
    for test in (test_backdate_then_delete_evasion, test_url_edit_alone_is_not_a_false_deletion,
                 test_update_status_no_longer_excuses_large_backdate, test_update_status_small_backdate_still_excused):
        try:
            test()
        except AssertionError as exc:
            failures.append(f"{test.__name__}: {exc}")
            print(f"{test.__name__}: FAIL -- {exc}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("\nAll L2 mutable-key-evasion fixture assertions pass.")
    sys.exit(0)


if __name__ == "__main__":
    main()
