import pytest

from griot.schema import LedgerLine, LedgerPage
from griot.verify import check_line, drop_ungrounded, numbers_in, verify_page


def line(raw, **kw):
    base = dict(raw_text=raw, entry_type="purchase", product="riz", quantity=None, unit=None,
                unit_price=None, total=None, customer=None, date="2026-10-12", legibility="high")
    return LedgerLine(**(base | kw))


def codes(issues):
    return {i.code for i in issues}


@pytest.mark.parametrize("text, expected", [
    ("Riz 2 sacs x 18.500 = 37.000", {2, 18500, 37000}),
    ("igname : 5,000", {5000}),
    ("Banane : 2 000F", {2000}),
    ("Viande de boeuf (1,5 Kg) : 4500f", {1.5, 4500}),
    ("1/2 sac : 9000", {1, 2, 0.5, 9000}),
])
def test_numbers_in(text, expected):
    assert numbers_in(text) == expected


def test_clean_line_has_no_issue():
    assert check_line(line("Riz 2 sacs x 18.500 = 37.000", quantity=2, unit="sac", unit_price=18500, total=37000)) == []


def test_arithmetic_mismatch_keeps_expected_value():
    issues = check_line(line("Sucre 3 kg x 700 = 2.200", quantity=3, unit="kg", unit_price=700, total=2200))
    (issue,) = issues
    assert issue.code == "arithmetic_mismatch" and issue.expected == 2100


def test_inferred_unit_price_is_flagged():
    # The model filled unit_price=15000 although only the total is written.
    issues = check_line(line("1 Sac de riz 15000", quantity=1, unit="sac", unit_price=15000, total=15000))
    assert codes(issues) == {"unit_price_not_in_text"}  # 15000 is written once: it is the total
    assert check_line(line("Gaz 1 bouteille x 5.500 = 5.500", quantity=1, unit="bouteille",
                           unit_price=5500, total=5500)) == []
    issues = check_line(line("Riz 2 sacs : 30000", quantity=2, unit="sac", unit_price=15000, total=30000))
    assert codes(issues) == {"unit_price_not_in_text"}


def test_misread_total_is_flagged():
    assert "total_not_in_text" in codes(check_line(line("Lait : 650", total=600)))


def test_missing_amount_and_debt_rules():
    issues = check_line(line("Hamed doit 1 pqt de sel", entry_type="debt", quantity=1, unit="paquet", customer="Hamed"))
    assert codes(issues) == {"missing_amount"}
    issues = check_line(line("crédit sucre 1.300", entry_type="debt", total=1300))
    assert codes(issues) == {"debt_without_customer"}


def test_units_and_fcfa_rules():
    issues = check_line(line("2,5 cartons : 2.502", quantity=2.5, unit="carton", total=2502))
    assert codes(issues) == {"fractional_count", "total_not_multiple_of_5"}
    assert "unknown_unit" in codes(check_line(line("3 zorro : 300", quantity=3, unit="zorro", total=300)))


def test_verdict_status_and_page_total():
    page = LedgerPage(rotation=0, page_date="2026-10-12", currency="XOF", written_total=4000, notes=None, lines=[
        line("Riz : 1.000", total=1000),
        line("Huile 2 L x 1.300", quantity=2, unit="L", unit_price=1300),  # total derivable
        line("Sucre 3 kg x 700 = 2.200", quantity=3, unit="kg", unit_price=700, total=2200),
        line("Savon : 250", total=250, legibility="low"),
    ])
    verdict = verify_page(page)
    assert [v.status for v in verdict.lines] == ["auto", "auto", "review", "review"]
    assert verdict.lines[1].computed_total == 2600
    assert verdict.lines_sum == 1000 + 2600 + 2200 + 250
    assert [i.code for i in verdict.issues] == ["page_total_mismatch"]
    assert [v.index for v in verdict.review_queue] == [2, 3]


def test_drop_ungrounded_keeps_written_values_only():
    page = LedgerPage(rotation=0, page_date="2026-10-12", currency="XOF", written_total=None, notes=None, lines=[
        line("Carton savon : 4.800", quantity=1, unit="carton", unit_price=4800, total=4800),
        line("Riz 2 sacs x 18.500 = 37.000", quantity=2, unit="sac", unit_price=18500, total=37000),
    ])
    grounded, dropped = drop_ungrounded(page)
    assert (grounded.lines[0].quantity, grounded.lines[0].unit_price, grounded.lines[0].total) == (None, None, 4800)
    assert grounded.lines[1] == page.lines[1]
    assert [(d["line"], d["field"]) for d in dropped] == [(1, "quantity"), (1, "unit_price")]


def test_missing_page_date_is_one_page_issue():
    verdict = verify_page(LedgerPage(rotation=0, page_date=None, currency="XOF", written_total=None, notes=None,
                                     lines=[line("Riz : 1.000", total=1000, date=None)] * 3))
    assert [i.code for i in verdict.issues] == ["missing_page_date"]
    assert verdict.review_queue == []
