import shutil

from griot.evaluate import FIELDS, evaluate_dir, evaluate_page, field_equal, run_usage
from griot.schema import LedgerPage
from tests.conftest import GROUND_TRUTH


def _truth(name="synthetic_page_01"):
    return LedgerPage.model_validate_json((GROUND_TRUTH / f"{name}.json").read_text(encoding="utf-8"))


def test_field_equal_normalises():
    assert field_equal("product", "Riz Parfumé", " riz  parfume ")
    assert field_equal("product", "œuf", "oeuf")
    assert field_equal("total", 37000, 37000.0)
    assert not field_equal("total", 37000, 3700)
    assert field_equal("customer", None, None)
    assert not field_equal("customer", "Koffi", None)


def test_perfect_prediction():
    gt = _truth()
    result = evaluate_page(gt, gt)
    assert result["matched_lines"] == len(gt.lines)
    assert all(result["correct"][f] == len(gt.lines) for f in FIELDS)


def test_missing_line_does_not_shift_alignment():
    gt = _truth()
    pred = gt.model_copy(update={"lines": gt.lines[:1] + gt.lines[2:]})  # model skipped line 2
    result = evaluate_page(gt, pred)
    assert result["matched_lines"] == len(gt.lines) - 1
    assert result["spurious_lines"] == 0
    assert result["correct"]["total"] == len(gt.lines) - 1


def test_field_error_is_counted():
    gt = _truth()
    lines = list(gt.lines)
    lines[0] = lines[0].model_copy(update={"quantity": 3})
    result = evaluate_page(gt, gt.model_copy(update={"lines": lines}))
    assert result["correct"]["quantity"] == len(gt.lines) - 1
    assert result["correct"]["total"] == len(gt.lines)


def test_evaluate_dir(tmp_path):
    shutil.copy(GROUND_TRUTH / "synthetic_page_01.json", tmp_path)  # other pages missing
    report = evaluate_dir(GROUND_TRUTH, tmp_path, "synthetic_page_*.json")
    assert report["pages"] == 3
    assert report["missing_pages"] == 2
    expected = len(_truth().lines) / report["gt_lines"]
    assert abs(report["overall_accuracy"] - round(expected, 4)) < 1e-9


def test_review_metrics_count_caught_booking_errors():
    gt = _truth()
    lines = list(gt.lines)
    lines[0] = lines[0].model_copy(update={"total": 3700})  # booking error, flagged
    lines[1] = lines[1].model_copy(update={"total": 390})  # booking error, only its page is flagged
    pred = gt.model_copy(update={"lines": lines})
    verdict = {"review_queue": [0], "issues": [{"code": "page_total_mismatch"}]}
    result = evaluate_page(gt, pred, verdict)
    assert (result["booked_error_lines"], result["booked_errors_caught_by_line"],
            result["booked_errors_caught_by_page"], result["page_questions"]) == (2, 1, 1, 1)


def test_run_usage_sums_model_calls_and_prices_them(tmp_path):
    from griot.audit import AuditLog
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.write("p1", "extracted", latency_s=10, prompt_tokens=2000, completion_tokens=3000)
    audit.write("p1", "reread", latency_s=20, changes=[])  # a re-read without token counts
    audit.write("p2", "extracted", latency_s=12, prompt_tokens=2000, completion_tokens=1000)
    audit.write("p2", "verified", review=[])
    usage = run_usage(tmp_path, 1.25, 4.25)
    assert usage == {"model_calls": 3, "tokens_in": 4000, "tokens_out": 4000, "seconds_per_page": 21.0,
                     "cost_usd": 0.022, "cost_usd_per_page": 0.011}
