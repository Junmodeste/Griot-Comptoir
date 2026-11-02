"""Book verified ledger lines into Odoo, with an audit entry for every write.

Usage:
    python -m griot.sync runs/2026-11-02 [--pages real_page_03] [--approve real_page_03:page,4]

Only lines the verification layer marked `auto` (or a human approved) are booked:
    purchase  -> confirmed purchase order, goods received into stock
    sale      -> confirmed sale order, goods delivered out of stock
    debt      -> posted customer invoice (a receivable, "créance")
    repayment -> customer payment, matched against the customer's open invoices
Expenses, `other` lines and lines waiting for review are held and logged, and a page with an
open page-level question (total mismatch, missing date) books nothing until a human answers it.

Each Odoo document carries a reference "GRIOT/<page>/L<lines>" that makes the sync
idempotent: running it twice books nothing new.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from griot.audit import AuditLog
from griot.odoo import OdooClient
from griot.schema import EntryType, LedgerPage
from griot.verify import PageVerdict, verify_page

DEFAULT_PARTNER = {
    EntryType.purchase: "Fournisseur divers",
    EntryType.sale: "Client comptoir",
}
GENERIC_PRODUCT = "Divers (non détaillé)"
BOOKING_ORDER = [EntryType.purchase, EntryType.sale, EntryType.debt, EntryType.repayment]
NO_TAX = [(6, 0, [])]  # informal retail: prices are taxes included, no VAT line


@dataclass
class BookLine:
    line_no: int
    product: str | None
    quantity: float
    price_unit: float
    description: str


@dataclass
class Document:
    kind: EntryType
    partner: str
    date: dt.date
    lines: list[BookLine]

    def ref(self, page_id: str) -> str:
        return f"GRIOT/{page_id}/L" + "-".join(str(l.line_no) for l in self.lines)

    @property
    def amount(self) -> float:
        return sum(l.quantity * l.price_unit for l in self.lines)


@dataclass
class SyncPlan:
    page_id: str
    documents: list[Document] = field(default_factory=list)
    held: list[tuple[int, str]] = field(default_factory=list)  # (line_no, reason)


PAGE = 0  # approval of the page-level questions (total mismatch, missing date)


def plan_page(page_id: str, page: LedgerPage, verdict: PageVerdict | None = None,
              approved: set[int] = frozenset(), today: dt.date | None = None) -> SyncPlan:
    """Decide what to book, without touching Odoo.

    `approved` holds the 1-based line numbers a human accepted, and PAGE when the human answered the
    page-level questions. Until then a page with an open question books nothing: a page total that
    does not match its lines is often the only sign of a misread amount.
    """
    verdict = verdict or verify_page(page)
    plan = SyncPlan(page_id)
    if verdict.issues and PAGE not in approved:
        reason = "page: " + ", ".join(i.code for i in verdict.issues)
        plan.held = [(v.index + 1, reason) for v in verdict.lines]
        return plan
    groups: dict[tuple, Document] = {}
    for line, v in zip(page.lines, verdict.lines):
        no = v.index + 1
        if v.status == "review" and no not in approved:
            plan.held.append((no, "review: " + ", ".join(i.code for i in v.issues) if v.issues else "review"))
            continue
        if line.entry_type in (EntryType.expense, EntryType.other):
            plan.held.append((no, f"not booked: {line.entry_type.value}"))
            continue
        if v.computed_total is None:
            plan.held.append((no, "no amount"))
            continue
        if line.entry_type in (EntryType.debt, EntryType.repayment) and not line.customer:
            plan.held.append((no, "no customer"))
            continue

        quantity = line.quantity or 1.0
        # Book the money that changed hands: the written total wins over quantity x unit price.
        price = v.computed_total / quantity
        partner = line.customer or DEFAULT_PARTNER.get(line.entry_type, "Client comptoir")
        date = line.date or page.page_date or today or dt.date.today()
        book = BookLine(no, line.product, quantity, price, line.raw_text)
        # One document per page, partner and kind; a repayment is always its own payment.
        key = (line.entry_type, partner, date, no if line.entry_type == EntryType.repayment else None)
        if key not in groups:
            groups[key] = Document(line.entry_type, partner, date, [])
            plan.documents.append(groups[key])
        groups[key].lines.append(book)
    # Goods come in before they go out, and a debt exists before it is paid back.
    plan.documents.sort(key=lambda d: BOOKING_ORDER.index(d.kind))
    return plan


class OdooSync:
    def __init__(self, odoo: OdooClient, audit: AuditLog):
        self.odoo = odoo
        self.audit = audit

    # --- master data -------------------------------------------------------------------------
    def partner(self, name: str) -> int:
        found = self.odoo.call("res.partner", "search", [("name", "=ilike", name)], limit=1)
        if found:
            return found[0]
        pid = self.odoo.call("res.partner", "create", {"name": name})
        self.audit.write("", "odoo_create", model="res.partner", id=pid, name=name)
        return pid

    def product(self, name: str | None) -> int:
        name = name or GENERIC_PRODUCT
        found = self.odoo.call("product.product", "search", [("name", "=ilike", name)], limit=1)
        if found:
            return found[0]
        values = {"name": name, "taxes_id": NO_TAX, "supplier_taxes_id": NO_TAX}
        if name == GENERIC_PRODUCT:
            values["type"] = "service"
        else:
            values.update(type="consu", is_storable=True)
        pid = self.odoo.call("product.product", "create", values)
        self.audit.write("", "odoo_create", model="product.product", id=pid, name=name)
        return pid

    # --- documents ---------------------------------------------------------------------------
    def apply(self, plan: SyncPlan) -> list[dict]:
        for no, reason in plan.held:
            self.audit.write(plan.page_id, "sync_held", line=no, reason=reason)
        results = []
        for doc in plan.documents:
            ref = doc.ref(plan.page_id)
            book = {EntryType.purchase: self._purchase, EntryType.sale: self._sale,
                    EntryType.debt: self._invoice, EntryType.repayment: self._payment}[doc.kind]
            existing = self._existing(doc.kind, ref)
            if existing:
                result = {"ref": ref, "kind": doc.kind.value, "status": "already_synced", **existing}
            else:
                result = {"ref": ref, "kind": doc.kind.value, "status": "booked", **book(doc, ref)}
            self.audit.write(plan.page_id, "odoo_" + result["status"], **result, partner=doc.partner,
                             amount=doc.amount, lines=[l.line_no for l in doc.lines])
            results.append(result)
        return results

    def _existing(self, kind: EntryType, ref: str) -> dict | None:
        model, ref_field = {EntryType.purchase: ("purchase.order", "origin"), EntryType.sale: ("sale.order", "origin"),
                            EntryType.debt: ("account.move", "ref"), EntryType.repayment: ("account.payment", "memo")}[kind]
        found = self.odoo.call(model, "search_read", [(ref_field, "=", ref)], fields=["name"], limit=1)
        return {"model": model, "id": found[0]["id"], "name": found[0]["name"]} if found else None

    def _order_lines(self, doc: Document, qty_field: str) -> list:
        return [(0, 0, {"product_id": self.product(l.product), qty_field: l.quantity, "price_unit": l.price_unit,
                        "name": l.description, "tax_ids": NO_TAX}) for l in doc.lines]

    def _move_goods(self, picking_ids: list[int]) -> list[str]:
        """Mark every move as done and validate: the notebook says the goods moved."""
        for move in self.odoo.call("stock.move", "search_read", [("picking_id", "in", picking_ids)],
                                   fields=["product_uom_qty"]):
            self.odoo.call("stock.move", "write", [move["id"]], {"quantity": move["product_uom_qty"], "picked": True})
        if picking_ids:
            self.odoo.call("stock.picking", "button_validate", picking_ids)
        return [p["name"] for p in self.odoo.call("stock.picking", "read", picking_ids, fields=["name"])]

    def _purchase(self, doc: Document, ref: str) -> dict:
        when = f"{doc.date.isoformat()} 12:00:00"
        oid = self.odoo.call("purchase.order", "create", {
            "partner_id": self.partner(doc.partner), "origin": ref, "date_order": when,
            "order_line": self._order_lines(doc, "product_qty")})
        self.odoo.call("purchase.order", "button_confirm", [oid])
        order = self.odoo.call("purchase.order", "read", [oid], fields=["name", "picking_ids"])[0]
        return {"model": "purchase.order", "id": oid, "name": order["name"],
                "pickings": self._move_goods(order["picking_ids"])}

    def _sale(self, doc: Document, ref: str) -> dict:
        when = f"{doc.date.isoformat()} 12:00:00"
        oid = self.odoo.call("sale.order", "create", {
            "partner_id": self.partner(doc.partner), "origin": ref, "date_order": when,
            "order_line": self._order_lines(doc, "product_uom_qty")})
        self.odoo.call("sale.order", "action_confirm", [oid])
        order = self.odoo.call("sale.order", "read", [oid], fields=["name", "picking_ids"])[0]
        return {"model": "sale.order", "id": oid, "name": order["name"],
                "pickings": self._move_goods(order["picking_ids"])}

    def _invoice(self, doc: Document, ref: str) -> dict:
        mid = self.odoo.call("account.move", "create", {
            "move_type": "out_invoice", "partner_id": self.partner(doc.partner), "ref": ref,
            "invoice_date": doc.date.isoformat(),
            "invoice_line_ids": [(0, 0, {"product_id": self.product(l.product), "quantity": l.quantity,
                                         "price_unit": l.price_unit, "name": l.description, "tax_ids": NO_TAX})
                                 for l in doc.lines]})
        self.odoo.call("account.move", "action_post", [mid])
        move = self.odoo.call("account.move", "read", [mid], fields=["name", "amount_residual"])[0]
        return {"model": "account.move", "id": mid, "name": move["name"], "receivable": move["amount_residual"]}

    def _payment(self, doc: Document, ref: str) -> dict:
        partner_id = self.partner(doc.partner)
        open_invoices = self.odoo.call(
            "account.move", "search",
            [("partner_id", "=", partner_id), ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
             ("payment_state", "in", ["not_paid", "partial"])], order="invoice_date asc, id asc")
        if open_invoices:
            ctx = {"active_model": "account.move", "active_ids": open_invoices}
            wizard = self.odoo.call("account.payment.register", "create",
                                    {"amount": doc.amount, "group_payment": True, "communication": ref,
                                     "payment_date": doc.date.isoformat()}, context=ctx)
            action = self.odoo.call("account.payment.register", "action_create_payments", [wizard], context=ctx)
            pid = action.get("res_id") if isinstance(action, dict) else None
            if pid:  # the wizard fills `memo` from the invoice; keep our reference for idempotency
                self.odoo.call("account.payment", "write", [pid], {"memo": ref})
        else:
            pid = self.odoo.call("account.payment", "create", {
                "payment_type": "inbound", "partner_type": "customer", "partner_id": partner_id,
                "amount": doc.amount, "memo": ref, "date": doc.date.isoformat()})
            self.odoo.call("account.payment", "action_post", [pid])
        name = self.odoo.call("account.payment", "read", [pid], fields=["name"])[0]["name"] if pid else None
        return {"model": "account.payment", "id": pid, "name": name, "matched_invoices": len(open_invoices)}


def parse_approvals(values: list[str]) -> dict[str, set[int]]:
    """'real_page_03:page,4,8' -> {'real_page_03': {PAGE, 4, 8}}"""
    approvals: dict[str, set[int]] = {}
    for value in values:
        page, _, lines = value.partition(":")
        approvals.setdefault(page, set()).update(PAGE if n == "page" else int(n) for n in lines.split(",") if n)
    return approvals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="Output folder of griot.agent")
    parser.add_argument("--pages", nargs="*", help="Page names to sync (default: all)")
    parser.add_argument("--approve", nargs="*", default=[],
                        help="Human answers: 'real_page_03:page,4' accepts the page questions and line 4")
    args = parser.parse_args(argv)

    approvals = parse_approvals(args.approve)
    sync = OdooSync(OdooClient.from_env(), AuditLog(args.run_dir / "audit.jsonl"))
    pages = sorted(p for p in args.run_dir.glob("*.json") if not p.name.endswith(".verdict.json"))
    for path in pages:
        if args.pages and path.stem not in args.pages:
            continue
        page = LedgerPage.model_validate_json(path.read_text(encoding="utf-8"))
        plan = plan_page(path.stem, page, approved=approvals.get(path.stem, set()))
        results = sync.apply(plan)
        booked = ", ".join(f"{r['name']} ({r['status']})" for r in results) or "nothing"
        print(f"{path.stem}: {booked}; held {len(plan.held)} line(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
