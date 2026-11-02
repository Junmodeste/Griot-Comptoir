import datetime as dt
import json
import os
import uuid

import pytest

from griot.audit import AuditLog
from griot.schema import EntryType, LedgerLine, LedgerPage
from griot.sync import PAGE, parse_approvals, plan_page


def line(raw, **kw):
    base = dict(raw_text=raw, entry_type="sale", product="riz", quantity=None, unit=None, unit_price=None,
                total=None, customer=None, date="2026-10-12", legibility="high")
    return LedgerLine(**(base | kw))


LEDGER = LedgerPage(rotation=0, page_date="2026-10-12", currency="XOF", written_total=None, notes=None, lines=[
    line("Riz 2 sacs x 18.500 = 37.000", quantity=2, unit="sac", unit_price=18500, total=37000),
    line("Huile : 1.300", product="huile", total=1300),
    line("Achat : 10 sacs riz x 17.000 = 170.000", entry_type="purchase", quantity=10, unit="sac",
         unit_price=17000, total=170000),
    line("Koffi doit : sucre 2 x 650 = 1.300", entry_type="debt", product="sucre", quantity=2, unit_price=650,
         total=1300, customer="Koffi"),
    line("Koffi a payé 1.000", entry_type="repayment", product=None, total=1000, customer="Koffi"),
    line("Transport : 500", entry_type="expense", product=None, total=500),
    line("Sucre 3 kg x 700 = 2.200", product="sucre", quantity=3, unit="kg", unit_price=700, total=2200),
])


def test_plan_groups_lines_into_documents_and_holds_the_rest():
    plan = plan_page("p1", LEDGER)

    kinds = [(d.kind, d.partner, [l.line_no for l in d.lines]) for d in plan.documents]
    assert kinds == [
        (EntryType.purchase, "Fournisseur divers", [3]),
        (EntryType.sale, "Client comptoir", [1, 2]),
        (EntryType.debt, "Koffi", [4]),
        (EntryType.repayment, "Koffi", [5]),
    ]
    assert plan.documents[1].ref("p1") == "GRIOT/p1/L1-2"
    assert plan.documents[1].lines[1].quantity == 1 and plan.documents[1].lines[1].price_unit == 1300
    assert dict(plan.held) == {6: "not booked: expense", 7: "review: arithmetic_mismatch"}


def test_human_approval_releases_a_review_line():
    plan = plan_page("p1", LEDGER, approved={7})
    assert 7 in [l.line_no for d in plan.documents for l in d.lines]
    assert 7 not in dict(plan.held)


def test_parse_approvals():
    assert parse_approvals(["real_page_03:page,8", "real_page_09:5"]) == {"real_page_03": {PAGE, 8},
                                                                         "real_page_09": {5}}


def test_open_page_question_holds_the_whole_page():
    page = LEDGER.model_copy(update={"written_total": 999})
    plan = plan_page("p1", page)
    assert plan.documents == [] and {r for _, r in plan.held} == {"page: page_total_mismatch"}
    assert len(plan_page("p1", page, approved={PAGE}).documents) == 4


def test_approved_arithmetic_mismatch_books_the_written_total():
    plan = plan_page("p1", LEDGER, approved={7})
    (booked,) = [l for d in plan.documents for l in d.lines if l.line_no == 7]
    assert booked.quantity * booked.price_unit == pytest.approx(2200)  # 3 x 700 is 2100, but 2.200 was paid


@pytest.mark.odoo
def test_sync_against_odoo(tmp_path):
    """Books a page into a real Odoo, then checks stock, receivable and idempotency."""
    if not os.environ.get("ODOO_URL"):
        pytest.skip("ODOO_URL not set")
    from griot.odoo import OdooClient
    from griot.sync import OdooSync

    odoo = OdooClient.from_env()
    tag = uuid.uuid4().hex[:6]  # fresh products and customer on every run
    lines = [l.model_copy(update={"product": f"{l.product} {tag}" if l.product else None,
                                  "customer": f"{l.customer} {tag}" if l.customer else None}) for l in LEDGER.lines]
    page = LEDGER.model_copy(update={"lines": lines})
    sync = OdooSync(odoo, AuditLog(tmp_path / "audit.jsonl"))
    page_id = f"test_{tag}"

    first = sync.apply(plan_page(page_id, page, today=dt.date(2026, 10, 12)))
    assert [r["status"] for r in first] == ["booked"] * 4

    riz = odoo.call("product.product", "search_read", [("name", "=", f"riz {tag}")], fields=["qty_available"])[0]
    assert riz["qty_available"] == 10 - 2  # received 10 sacs, sold 2
    invoice = odoo.call("account.move", "search_read", [("ref", "=", f"GRIOT/{page_id}/L4")],
                        fields=["amount_total", "amount_residual", "payment_state"])[0]
    assert (invoice["amount_total"], invoice["amount_residual"]) == (1300, 300)  # 1.000 paid back

    again = sync.apply(plan_page(page_id, page, today=dt.date(2026, 10, 12)))
    assert [r["status"] for r in again] == ["already_synced"] * 4


@pytest.mark.odoo
def test_units_convert_or_hold(tmp_path):
    """Oil bought in litres and sold in centilitres converts; oil sold by the 'bidon' is held."""
    if not os.environ.get("ODOO_URL"):
        pytest.skip("ODOO_URL not set")
    from griot.odoo import OdooClient
    from griot.sync import OdooSync

    odoo = OdooClient.from_env()
    tag = uuid.uuid4().hex[:6]
    oil = f"huile {tag}"
    page = LEDGER.model_copy(update={"lines": [
        line("Achat huile 25 L x 1.200 = 30.000", entry_type="purchase", product=oil, quantity=25, unit="L",
             unit_price=1200, total=30000),
        line("Huile 500 cl x 15 = 7.500", product=oil, quantity=500, unit="cl", unit_price=15, total=7500),
        line("Huile 1 bidon x 6.000 = 6.000", product=oil, quantity=1, unit="bidon", unit_price=6000, total=6000),
    ]})
    audit = AuditLog(tmp_path / "audit.jsonl")

    results = OdooSync(odoo, audit).apply(plan_page(f"units_{tag}", page))

    assert [r["status"] for r in results] == ["booked", "booked"]
    product = odoo.call("product.product", "search_read", [("name", "=", oil)], fields=["qty_available", "uom_id"])[0]
    assert product["uom_id"][1] == "L" and product["qty_available"] == pytest.approx(20)  # 25 L - 500 cl
    held = [e for e in map(json.loads, audit.path.read_text().splitlines()) if e["event"] == "sync_held"]
    assert [(e["line"], e["reason"].split(":")[0]) for e in held] == [(3, "unit_mismatch")]
