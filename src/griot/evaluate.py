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
NUMERIC_FIELDS = {"quantity", "unit_price", "total"}


def _norm_text(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


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


def evaluate_page(gt: LedgerPage, pred: LedgerPage) -> dict:
    pairs = align(gt.lines, pred.lines)
    correct = {f: sum(p is not None and field_equal(f, getattr(g, f), getattr(p, f)) for g, p in pairs) for f in FIELDS}
    matched = sum(p is not None for _, p in pairs)
    return {
        "gt_lines": len(gt.lines),
        "matched_lines": matched,
        "spurious_lines": len(pred.lines) - matched,
        "correct": correct,
    }


def evaluate_dir(gt_dir: Path, pred_dir: Path) -> dict:
    pages, totals = {}, {"gt_lines": 0, "matched_lines": 0, "spurious_lines": 0, "missing_pages": 0}
    correct = dict.fromkeys(FIELDS, 0)
    for gt_path in sorted(gt_dir.glob("*.json")):
        gt = LedgerPage.model_validate_json(gt_path.read_text(encoding="utf-8"))
        pred_path = pred_dir / gt_path.name
        if pred_path.exists():
            pred = LedgerPage.model_validate_json(pred_path.read_text(encoding="utf-8"))
        else:
            totals["missing_pages"] += 1
            pred = LedgerPage(page_date=None, currency="XOF", lines=[], notes=None)
        result = evaluate_page(gt, pred)
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
        "per_page": pages,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gt", type=Path, default=Path("eval/ground_truth"))
    parser.add_argument("--pred", type=Path, required=True)
    parser.add_argument("--out", type=Path, help="Write the full report as JSON")
    args = parser.parse_args(argv)

    report = evaluate_dir(args.gt, args.pred)
    print(f"pages={report['pages']} lines={report['gt_lines']} recall={report['line_recall']} "
          f"spurious={report['spurious_lines']} missing_pages={report['missing_pages']}")
    for field, acc in report["field_accuracy"].items():
        print(f"  {field:<11} {acc}")
    print(f"  {'OVERALL':<11} {report['overall_accuracy']}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
