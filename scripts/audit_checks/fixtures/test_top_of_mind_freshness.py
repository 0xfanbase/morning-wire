"""Red/green fixture for check_top_of_mind_freshness: the Top-of-mind
callout must trace to the Today pool it renders beside.

RED cases are built from the shape of the real 2026-07-24 live finding: an
enrichment session working a multi-day backlog wrote the callout from OLD
items (a FinCEN comment-period story, a sanctioned-exchange story), while
the actual Today pool -- items first seen on generated_at's HKT day --
contained neither. The page then showed a confident "top of mind today"
about stories absent from the view beside it.

GREEN cases: a callout genuinely derived from a Today-pool item's own
summary/so_what (the recipe's required sourcing), and the always-legitimate
empty callout.

Run manually: python3 scripts/audit_checks/fixtures/test_top_of_mind_freshness.py
Exits non-zero if any assertion fails.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# generated_at 21:59Z = 05:59 HKT the NEXT day -- the Today pool is the +8h day.
GENERATED_AT = "2026-07-23T21:59:00+00:00"
IN_POOL_FIRST_SEEN = "2026-07-23T21:59:00+00:00"    # HKT day 2026-07-24 == generation day
OLD_FIRST_SEEN = "2026-07-21T21:59:00+00:00"        # HKT day 2026-07-22 -- archive, not Today


def _item(item_id, title, summary, so_what, first_seen):
    return {"id": item_id, "title": title, "summary": summary, "so_what": so_what,
            "first_seen": first_seen, "jurisdiction": "GLOBAL", "type": "news", "priority": "high"}


FATF_TODAY_ITEM = _item(
    "global-example-fatf-0001",
    "What FATF's 7th Crypto Compliance Report Card Means",
    "An analysis of the Financial Action Task Force (FATF)'s seventh progress report on virtual "
    "assets found that although most countries have now passed crypto anti-money-laundering laws, "
    "enforcement lags far behind, with fewer than one in ten fully meeting the standards.",
    "The gap between crypto anti-money-laundering laws on paper and their enforcement means a "
    "compliance function should base virtual-asset counterparty risk ratings on whether a "
    "jurisdiction genuinely supervises and enforces its rules.",
    IN_POOL_FIRST_SEEN,
)

FINCEN_OLD_ITEM = _item(
    "us-example-fincen-0001",
    "FinCEN extends comment period on Huione Group definition expansion",
    "The United States Financial Crimes Enforcement Network reopened the comment period to "
    "2 August on widening the official name coverage of a money-laundering network for "
    "correspondent-banking screening.",
    "Screening teams gain time to comment on the widened name coverage before it becomes final.",
    OLD_FIRST_SEEN,
)

STALE_CALLOUT = ("The United States Financial Crimes Enforcement Network reopened comments to "
                 "2 August on widening a money-laundering network's official name coverage for "
                 "correspondent-banking screening.")

DERIVED_CALLOUT = ("The Financial Action Task Force (FATF)'s seventh progress report on virtual "
                   "assets finds most countries now have crypto anti-money-laundering laws but "
                   "enforcement lags far behind, so risk ratings should weigh whether rules are "
                   "genuinely enforced.")


def _repo(tmp, top_of_mind, items):
    root = Path(tmp)
    (root / "data").mkdir()
    (root / "data" / "digest.json").write_text(json.dumps({
        "generated_at": GENERATED_AT, "top_of_mind": top_of_mind, "items": items,
    }), encoding="utf-8")
    return root


def main():
    failures = []
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "audit_checks"))
    import check_top_of_mind_freshness as check

    # RED: the callout describes an item that exists in the DIGEST but not in
    # the Today pool -- the exact live 2026-07-24 shape.
    with tempfile.TemporaryDirectory() as tmp:
        findings = check.run(_repo(tmp, STALE_CALLOUT, [FATF_TODAY_ITEM, FINCEN_OLD_ITEM]))
        print(f"RED case (callout from backlog item): {len(findings)} finding(s)")
        if not any(f["severity"] == "warn" and "doesn't trace" in f["title"] for f in findings):
            failures.append("RED FIXTURE FAILED: a callout sourced from an item OUTSIDE the Today "
                             "pool was not flagged")

    # RED: non-empty callout over an empty Today pool.
    with tempfile.TemporaryDirectory() as tmp:
        findings = check.run(_repo(tmp, STALE_CALLOUT, [FINCEN_OLD_ITEM]))
        print(f"RED case (non-empty callout, empty pool): {len(findings)} finding(s)")
        if not any(f["severity"] == "warn" and "no items at all" in f["title"] for f in findings):
            failures.append("RED FIXTURE FAILED: a non-empty callout beside an empty Today pool was "
                             "not flagged")

    # GREEN: callout derived from a Today-pool item's own summary/so_what.
    with tempfile.TemporaryDirectory() as tmp:
        findings = check.run(_repo(tmp, DERIVED_CALLOUT, [FATF_TODAY_ITEM, FINCEN_OLD_ITEM]))
        print(f"GREEN case (derived from Today pool): {len(findings)} finding(s)")
        if findings:
            failures.append(f"GREEN FIXTURE FAILED: a callout genuinely derived from a Today-pool "
                             f"item was flagged -- {findings}")

    # GREEN: empty callout is always legitimate (quiet days), even with items present.
    with tempfile.TemporaryDirectory() as tmp:
        findings = check.run(_repo(tmp, "", [FATF_TODAY_ITEM]))
        print(f"GREEN case (empty callout): {len(findings)} finding(s)")
        if findings:
            failures.append(f"GREEN FIXTURE FAILED: an empty callout produced findings -- {findings}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f" - {f}")
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
