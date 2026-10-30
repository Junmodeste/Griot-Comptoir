import shutil

from griot.evaluate import FIELDS, evaluate_dir, evaluate_page, field_equal
from griot.schema import LedgerPage
from tests.conftest import GROUND_TRUTH


def _truth(name="synthetic_page_01"):
    return LedgerPage.model_validate_json((GROUND_TRUTH / f"{name}.json").read_text(encoding="utf-8"))


def test_field_equal_normalises():
    assert field_equal("product", "Riz Parfumé", " riz  parfume ")
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
    report = evaluate_dir(GROUND_TRUTH, tmp_path)
    assert report["pages"] == 3
    assert report["missing_pages"] == 2
    expected = len(_truth().lines) / report["gt_lines"]
    assert abs(report["overall_accuracy"] - round(expected, 4)) < 1e-9
