import json
import shutil

import pytest

from griot.review import ReviewError, ReviewStore
from griot.sync import PAGE
from tests.conftest import GROUND_TRUTH, SAMPLES


class FakeSync:
    def __init__(self, audit):
        self.audit = audit
        self.plans = []

    def apply(self, plan):
        self.plans.append(plan)
        for doc in plan.documents:
            self.audit.write(plan.page_id, "odoo_booked", name=f"DOC-{doc.kind.value}", kind=doc.kind.value,
                             amount=doc.amount)
        return []


@pytest.fixture
def store(tmp_path):
    shutil.copy(GROUND_TRUTH / "synthetic_page_01.json", tmp_path / "synthetic_page_01.json")
    syncs = []

    def factory(audit):
        syncs.append(FakeSync(audit))
        return syncs[-1]

    s = ReviewStore(tmp_path, SAMPLES, factory)
    s.syncs = syncs
    return s


def _events(store):
    return [json.loads(l) for l in store.audit.path.read_text(encoding="utf-8").splitlines()]


def test_view_lists_questions_and_plan(store):
    view = store.view("synthetic_page_01")
    assert [l["status"] for l in view["lines"]].count("review") == 1  # line 6: 4 x 450 written as 2.000
    assert {"line": 6, "reason": "review: arithmetic_mismatch"} in view["plan"]["held"]
    assert store.summary() == [{"id": "synthetic_page_01", "lines": 6, "review": 1, "page_questions": 0, "booked": []}]
    assert store.image("synthetic_page_01").name == "synthetic_page_01.jpg"


def test_correction_is_validated_reverified_and_audited(store):
    view = store.correct("synthetic_page_01", 6, "total", 1800)
    assert view["lines"][5]["approved"] and 6 in [l for d in view["plan"]["documents"] for l in d["lines"]]
    assert [e["event"] for e in _events(store)][-2:] == ["human_correction", "human_approval"]
    assert _events(store)[-2] | {"ts": None} == {"ts": None, "page": "synthetic_page_01", "event": "human_correction",
                                                 "line": 6, "field": "total", "before": 2000.0, "after": 1800.0}
    with pytest.raises(ReviewError):
        store.correct("synthetic_page_01", 6, "quantity", "beaucoup")
    with pytest.raises(ReviewError):
        store.correct("synthetic_page_01", 6, "raw_text", "edited")  # the transcription itself is not editable


def test_approval_then_booking(store):
    store.approve("synthetic_page_01", 6)
    store.approve("synthetic_page_01", PAGE)
    assert json.loads((store.run_dir / "approvals.json").read_text()) == {"synthetic_page_01": [PAGE, 6]}

    view = store.book("synthetic_page_01")

    booked_lines = [l.line_no for d in store.syncs[0].plans[0].documents for l in d.lines]
    assert 6 in booked_lines
    assert {b["name"] for b in view["booked"]} == {"DOC-sale", "DOC-debt", "DOC-repayment"}


def test_unknown_page_is_404(store):
    with pytest.raises(ReviewError) as info:
        store.view("../secret")
    assert info.value.status == 404


def test_page_date_fills_undated_lines_and_clears_the_question(store):
    page = json.loads((store.run_dir / "synthetic_page_01.json").read_text())
    page["page_date"] = None
    for line in page["lines"]:
        line["date"] = None
    (store.run_dir / "synthetic_page_01.json").write_text(json.dumps(page))
    assert [i["code"] for i in store.view("synthetic_page_01")["page_issues"]] == ["missing_page_date"]

    view = store.set_page_date("synthetic_page_01", "2026-10-15")

    assert view["page_issues"] == [] and {l["date"] for l in view["page"]["lines"]} == {"2026-10-15"}
    assert _events(store)[-1]["field"] == "page_date"
