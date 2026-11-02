"""Per-field accuracy of predicted ledger pages against hand-written ground truth.

Usage:
    python -m griot.evaluate --gt eval/ground_truth --pred eval/predictions/muse-spark-1.3

Lines are aligned greedily (a model may skip or add a line, so index alignment
would shift every later line). A missing ground-truth line counts as wrong on
every field; extra predicted lines are reported as `spurious_lines`.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path

from griot.schema import LedgerLine, LedgerPage

FIELDS = ["entry_type", "product", "quantity", "unit", "unit_price", "total", "customer", "date"]
TEXT_FIELDS = {"product", "unit", "customer"}
# Fields that change what gets booked: an error here must reach the review queue.
BOOKED_FIELDS = ["entry_type", "quantity", "unit_price", "total", "customer"]
NUMERIC_FIELDS = {"quantity", "unit_price", "total"}


def _norm_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold().replace("œ", "oe").replace("æ", "ae"))
    plain = "".join(c if c.isalnum() else " " for c in folded if not unicodedata.combining(c))
    return " ".join(plain.split())


def field_equal(field: str, expected, predicted) -> bool:
    if expected is None or predicted is None:
        return expected is None and predicted is None
    if field in NUMERIC_FIELDS:
        return abs(float(expected) - float(predicted)) <= 1e-6 * max(1.0, abs(float(expected)))
    if field in TEXT_FIELDS:
        return _norm_text(expected) == _norm_text(predicted)
    return expected == predicted


def _score(gt: LedgerLine, pred: LedgerLine) -> int:
    return sum(field_equal(f, getattr(gt, f), getattr(pred, f)) for f in FIELDS)


def align(gt_lines: list[LedgerLine], pred_lines: list[LedgerLine]) -> list[tuple[LedgerLine, LedgerLine | None]]:
    """Greedy best-match: highest field agreement first, ties broken by position."""
    candidates = sorted(
        ((_score(g, p), -abs(i - j), i, j) for i, g in enumerate(gt_lines) for j, p in enumerate(pred_lines)),
        reverse=True,
    )
    matched: dict[int, int] = {}
    used: set[int] = set()
    for score, _, i, j in candidates:
        if score == 0 or i in matched or j in used:
            continue
        matched[i] = j
        used.add(j)
    return [(g, pred_lines[matched[i]] if i in matched else None) for i, g in enumerate(gt_lines)]


def evaluate_page(gt: LedgerPage, pred: LedgerPage, verdict: dict | None = None) -> dict:
    pairs = align(gt.lines, pred.lines)
    correct = {f: sum(p is not None and field_equal(f, getattr(g, f), getattr(p, f)) for g, p in pairs) for f in FIELDS}
    matched = sum(p is not None for _, p in pairs)
    review = {}
    if verdict is not None:
        flagged = set(verdict["review_queue"])
        index = {id(p): j for j, p in enumerate(pred.lines)}
        booked_errors = [p is None or not all(field_equal(f, getattr(g, f), getattr(p, f)) for f in BOOKED_FIELDS)
                         for g, p in pairs]
        caught_by_line = [err and p is not None and index[id(p)] in flagged for err, (_, p) in zip(booked_errors, pairs)]
        review = {
            "booked_error_lines": sum(booked_errors),
            "booked_errors_caught_by_line": sum(caught_by_line),
            # Not flagged itself, but its page is (e.g. a page total mismatch): a human looks at the page.
            "booked_errors_caught_by_page": sum(err and not hit and bool(verdict["issues"])
                                                for err, hit in zip(booked_errors, caught_by_line)),
            "flagged_lines": len(flagged),
            "page_questions": len(verdict["issues"]),
        }
    return {
        "gt_lines": len(gt.lines),
        "matched_lines": matched,
        "spurious_lines": len(pred.lines) - matched,
        "correct": correct,
        "written_total_ok": field_equal("total", gt.written_total, pred.written_total),
        **review,
    }


def evaluate_dir(gt_dir: Path, pred_dir: Path, pattern: str = "*.json") -> dict:
    pages, totals = {}, {"gt_lines": 0, "matched_lines": 0, "spurious_lines": 0, "missing_pages": 0}
    correct = dict.fromkeys(FIELDS, 0)
    for gt_path in sorted(gt_dir.glob(pattern)):
        gt = LedgerPage.model_validate_json(gt_path.read_text(encoding="utf-8"))
        pred_path = pred_dir / gt_path.name
        if pred_path.exists():
            pred = LedgerPage.model_validate_json(pred_path.read_text(encoding="utf-8"))
            verdict_path = pred_dir / f"{gt_path.stem}.verdict.json"
            verdict = json.loads(verdict_path.read_text(encoding="utf-8")) if verdict_path.exists() else None
        else:
            totals["missing_pages"] += 1
            pred = LedgerPage(rotation=0, page_date=None, currency="XOF", lines=[], written_total=None, notes=None)
            verdict = None
        result = evaluate_page(gt, pred, verdict)
        pages[gt_path.stem] = result
        for key in ("gt_lines", "matched_lines", "spurious_lines"):
            totals[key] += result[key]
        for f in FIELDS:
            correct[f] += result["correct"][f]

    n = totals["gt_lines"]
    accuracy = {f: round(correct[f] / n, 4) if n else None for f in FIELDS}
    all_fields = sum(correct.values()) / (n * len(FIELDS)) if n else None
    return {
        "pages": len(pages),
        **totals,
        "line_recall": round(totals["matched_lines"] / n, 4) if n else None,
        "field_accuracy": accuracy,
        "overall_accuracy": round(all_fields, 4) if all_fields is not None else None,
        "written_total_accuracy": round(sum(r["written_total_ok"] for r in pages.values()) / len(pages), 4)
        if pages else None,
        **_review_summary(pages, totals["gt_lines"]),
        "per_page": pages,
    }


def _review_summary(pages: dict, gt_lines: int) -> dict:
    with_verdict = [r for r in pages.values() if "flagged_lines" in r]
    if not with_verdict:
        return {}
    errors = sum(r["booked_error_lines"] for r in with_verdict)
    by_line = sum(r["booked_errors_caught_by_line"] for r in with_verdict)
    by_page = sum(r["booked_errors_caught_by_page"] for r in with_verdict)
    return {
        "flagged_lines": sum(r["flagged_lines"] for r in with_verdict),
        "page_questions": sum(r["page_questions"] for r in with_verdict),
        "review_rate": round(sum(r["flagged_lines"] for r in with_verdict) / gt_lines, 4),
        "booked_error_lines": errors,
        "booked_error_recall_line": round(by_line / errors, 4) if errors else None,
        "booked_error_recall_line_or_page": round((by_line + by_page) / errors, 4) if errors else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gt", type=Path, default=Path("eval/ground_truth"))
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--pages", default="*.json", help="Ground-truth glob, e.g. 'real_page_*.json'")
    parser.add_argument("--out", type=Path, help="Write the full report as JSON")
    args = parser.parse_args(argv)

    report = evaluate_dir(args.gt, args.pred, args.pages)
    print(f"pages={report['pages']} lines={report['gt_lines']} recall={report['line_recall']} "
          f"spurious={report['spurious_lines']} missing_pages={report['missing_pages']}")
    for field, acc in report["field_accuracy"].items():
        print(f"  {field:<11} {acc}")
    print(f"  {'OVERALL':<11} {report['overall_accuracy']}")
    print(f"  page written_total {report['written_total_accuracy']}")
    if "review_rate" in report:
        print(f"review: {report['flagged_lines']} lines ({report['review_rate']:.0%}) + {report['page_questions']} "
              f"page questions; lines with a booking error: {report['booked_error_lines']}, caught by a flagged "
              f"line: {report['booked_error_recall_line']}, by line or page: {report['booked_error_recall_line_or_page']}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
