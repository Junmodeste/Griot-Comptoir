"""Deterministic checks on an extracted LedgerPage.

Every line gets a list of issues, a confidence score and a status: `auto` lines
can be booked without a human, `review` lines go to the review queue. No model
call happens here, so the same page always gives the same verdict.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum

from griot.schema import EntryType, LedgerLine, LedgerPage, Legibility

KNOWN_UNITS = {
    "sac", "kg", "g", "l", "cl", "boîte", "paquet", "carton", "tas", "bidon", "bouteille", "plaquette",
    "sachet", "régime", "tête", "boule", "tablette", "plat", "bocal", "pic", "pièce", "seau", "litre",
}
# Units that cannot be split: 2.5 cartons is suspicious, 1.5 kg is not.
COUNT_UNITS = {"carton", "bouteille", "plaquette", "boîte", "bidon", "bocal", "tête", "boule", "plat", "pièce"}
REVIEW_THRESHOLD = 0.8
AMOUNT_TOLERANCE = 0.5  # FCFA
_NUMBER = re.compile(r"\d+(?:[.,  ]\d{3})+(?![\d])|\d+(?:,\d+)?")


class Severity(str, Enum):
    high = "high"  # cannot be booked as is
    medium = "medium"  # probably wrong
    low = "low"  # worth a glance


PENALTY = {Severity.high: 0.3, Severity.medium: 0.6, Severity.low: 0.85}
LEGIBILITY_FACTOR = {Legibility.high: 1.0, Legibility.medium: 0.85, Legibility.low: 0.5}


@dataclass
class Issue:
    code: str
    severity: Severity
    message: str
    expected: float | None = None


@dataclass
class LineVerdict:
    index: int
    issues: list[Issue]
    confidence: float
    status: str  # "auto" | "review"
    computed_total: float | None  # amount to book: written total, else quantity x unit_price


@dataclass
class PageVerdict:
    lines: list[LineVerdict]
    issues: list[Issue] = field(default_factory=list)
    lines_sum: float = 0.0

    @property
    def review_queue(self) -> list[LineVerdict]:
        return [v for v in self.lines if v.status == "review"]

    def to_dict(self) -> dict:
        return asdict(self) | {"review_queue": [v.index for v in self.review_queue]}


def number_tokens(text: str) -> list[float]:
    """Numbers as a shopkeeper writes them: '2.500', '5,000', '2 500' = 2500; '1,5' = 1.5; '1/2' = 0.5."""
    found = [float(a) / float(b) for a, b in re.findall(r"(\d+)/(\d+)", text) if float(b)]
    for token in _NUMBER.findall(text):
        if re.fullmatch(r"\d+(?:[.,  ]\d{3})+", token):
            found.append(float(re.sub(r"\D", "", token)))
        else:
            found.append(float(token.replace(",", ".")))
    return found


def numbers_in(text: str) -> set[float]:
    return set(number_tokens(text))


def is_written(name: str, line: LedgerLine) -> bool:
    """Is this value written on the line? A unit price equal to the total must be written twice
    ("1 x 5.500 = 5.500"); otherwise the model copied the total into it."""
    value = getattr(line, name)
    count = sum(_close(value, w) for w in number_tokens(line.raw_text))
    if name == "unit_price" and line.total is not None and _close(value, line.total):
        return count >= 2
    return count >= 1


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= AMOUNT_TOLERANCE


def check_line(line: LedgerLine) -> list[Issue]:
    issues: list[Issue] = []
    q, pu, total = line.quantity, line.unit_price, line.total

    if q is not None and pu is not None and total is not None and not _close(q * pu, total):
        issues.append(Issue("arithmetic_mismatch", Severity.high,
                            f"{q:g} x {pu:g} = {q * pu:g}, but {total:g} is written", expected=q * pu))
    if total is None and (q is None or pu is None):
        issues.append(Issue("missing_amount", Severity.high, "No amount written and none can be computed"))

    # Grounding: every number the model returns must be readable in the line itself.
    for name, value in (("quantity", q), ("unit_price", pu), ("total", total)):
        if value is not None and not is_written(name, line):
            severity = Severity.medium if name == "unit_price" else Severity.high
            issues.append(Issue(f"{name}_not_in_text", severity, f"{name}={value:g} does not appear in the line"))

    for name, value in (("unit_price", pu), ("total", total)):
        if value is not None and value % 5:
            issues.append(Issue(f"{name}_not_multiple_of_5", Severity.low,
                                f"{name}={value:g}: FCFA amounts are multiples of 5"))
    if q is not None and q <= 0:
        issues.append(Issue("non_positive_quantity", Severity.high, f"quantity={q:g}"))

    unit = (line.unit or "").casefold()
    if unit and unit not in KNOWN_UNITS:
        issues.append(Issue("unknown_unit", Severity.low, f"unit '{line.unit}' is not in the unit list"))
    if unit in COUNT_UNITS and q is not None and not float(q).is_integer():
        issues.append(Issue("fractional_count", Severity.medium, f"{q:g} {line.unit}: expected a whole number"))

    if line.entry_type in (EntryType.debt, EntryType.repayment) and not line.customer:
        issues.append(Issue("debt_without_customer", Severity.high, "A debt or repayment needs a customer name"))
    if line.entry_type == EntryType.other:
        issues.append(Issue("unclassified_entry", Severity.medium, "Entry type is 'other'"))
    return issues


def drop_ungrounded(page: LedgerPage) -> tuple[LedgerPage, list[dict]]:
    """Null a quantity or unit_price that is not written on its line (the model inferred it).

    A total is never dropped: without it the line cannot be booked, so it goes to review instead.
    """
    lines, dropped = [], []
    for index, line in enumerate(page.lines):
        update = {name: None for name in ("quantity", "unit_price")
                  if getattr(line, name) is not None and not is_written(name, line)}
        dropped += [{"line": index + 1, "field": name, "before": getattr(line, name), "after": None} for name in update]
        lines.append(line.model_copy(update=update))
    return page.model_copy(update={"lines": lines}), dropped


def verify_page(page: LedgerPage) -> PageVerdict:
    verdicts = []
    for index, line in enumerate(page.lines):
        issues = check_line(line)
        if line.date is None and page.page_date is not None:
            issues.append(Issue("missing_date", Severity.medium, "Line has no date although the page has one"))
        confidence = LEGIBILITY_FACTOR[line.legibility]
        for issue in issues:
            confidence *= PENALTY[issue.severity]
        needs_review = confidence < REVIEW_THRESHOLD or any(i.severity == Severity.high for i in issues)
        computed = line.total
        if computed is None and line.quantity is not None and line.unit_price is not None:
            computed = line.quantity * line.unit_price
        verdicts.append(LineVerdict(index, issues, round(confidence, 3), "review" if needs_review else "auto", computed))

    lines_sum = sum(v.computed_total or 0 for v, line in zip(verdicts, page.lines) if line.entry_type != EntryType.other)
    verdict = PageVerdict(verdicts, lines_sum=lines_sum)
    if page.page_date is None and any(line.date is None for line in page.lines):
        # One decision for the whole page, not one per line.
        verdict.issues.append(Issue("missing_page_date", Severity.medium,
                                    "No date found: check the top of the page, else ask the shopkeeper"))
    if page.written_total is not None and not _close(lines_sum, page.written_total):
        verdict.issues.append(Issue("page_total_mismatch", Severity.medium,
                                    f"Lines add up to {lines_sum:g}, page total says {page.written_total:g}",
                                    expected=lines_sum))
    return verdict
