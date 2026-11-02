"""Local review page: a human answers the agent's questions, then books the page into Odoo.

Usage:
    python -m griot.review runs/demo --images data/samples   # then open http://127.0.0.1:8000

The server only listens on 127.0.0.1. Every correction and approval is appended to the run's
audit.jsonl; approvals are kept in approvals.json so `griot.sync` sees them too.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote

from pydantic import ValidationError

from griot.audit import AuditLog
from griot.schema import LedgerLine, LedgerPage
from griot.sync import PAGE, OdooSync, plan_page
from griot.verify import verify_page

EDITABLE = {"entry_type", "product", "quantity", "unit", "unit_price", "total", "customer", "date"}


class ReviewError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


class ReviewStore:
    """Reads and writes one agent run folder. No HTTP here, so it is easy to test."""

    def __init__(self, run_dir: Path, images: Path, sync_factory=None):
        self.run_dir = run_dir
        self.images = images
        self.audit = AuditLog(run_dir / "audit.jsonl")
        self._sync_factory = sync_factory  # builds an OdooSync on first booking

    # --- state -------------------------------------------------------------------------------
    def page_ids(self) -> list[str]:
        return sorted(p.stem for p in self.run_dir.glob("*.json")
                      if not p.name.endswith(".verdict.json") and p.name != "approvals.json")

    def load(self, page_id: str) -> LedgerPage:
        path = self.run_dir / f"{page_id}.json"
        if page_id not in self.page_ids():
            raise ReviewError(HTTPStatus.NOT_FOUND, f"unknown page {page_id}")
        return LedgerPage.model_validate_json(path.read_text(encoding="utf-8"))

    def approvals(self) -> dict[str, list[int]]:
        path = self.run_dir / "approvals.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def booked(self, page_id: str) -> list[dict]:
        path = self.run_dir / "audit.jsonl"
        if not path.exists():
            return []
        events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
        return [{"name": e["name"], "kind": e["kind"], "amount": e["amount"]} for e in events
                if e["page"] == page_id and e["event"] in ("odoo_booked", "odoo_already_synced")]

    def view(self, page_id: str) -> dict:
        page = self.load(page_id)
        verdict = verify_page(page)
        approved = set(self.approvals().get(page_id, []))
        plan = plan_page(page_id, page, verdict, approved=approved)
        return {
            "id": page_id,
            "page": json.loads(page.model_dump_json()),
            "lines_sum": verdict.lines_sum,
            "page_issues": [{"code": i.code, "message": i.message} for i in verdict.issues],
            "page_approved": PAGE in approved,
            "lines": [{"status": v.status, "confidence": v.confidence, "approved": v.index + 1 in approved,
                       "issues": [{"code": i.code, "severity": i.severity.value, "message": i.message}
                                  for i in v.issues]} for v in verdict.lines],
            "plan": {"documents": [{"kind": d.kind.value, "partner": d.partner, "amount": d.amount,
                                    "lines": [l.line_no for l in d.lines]} for d in plan.documents],
                     "held": [{"line": n, "reason": r} for n, r in plan.held]},
            "booked": self.booked(page_id),
        }

    def summary(self) -> list[dict]:
        out = []
        for page_id in self.page_ids():
            view = self.view(page_id)
            out.append({"id": page_id, "lines": len(view["lines"]),
                        "review": sum(l["status"] == "review" and not l["approved"] for l in view["lines"]),
                        "page_questions": 0 if view["page_approved"] else len(view["page_issues"]),
                        "booked": [b["name"] for b in view["booked"]]})
        return out

    def image(self, page_id: str) -> Path:
        self.load(page_id)
        for folder in (self.run_dir / "upright", self.images):
            for path in sorted(folder.glob(f"{page_id}.*")) if folder.exists() else []:
                return path
        raise ReviewError(HTTPStatus.NOT_FOUND, f"no photo for {page_id}")

    # --- actions -----------------------------------------------------------------------------
    def correct(self, page_id: str, line_no: int, field: str, value) -> dict:
        if field not in EDITABLE:
            raise ReviewError(HTTPStatus.BAD_REQUEST, f"field {field} cannot be edited")
        page = self.load(page_id)
        if not 1 <= line_no <= len(page.lines):
            raise ReviewError(HTTPStatus.NOT_FOUND, f"no line {line_no}")
        old = page.lines[line_no - 1]
        data = json.loads(old.model_dump_json()) | {field: None if value in ("", None) else value}
        try:
            new = LedgerLine.model_validate(data)
        except ValidationError as exc:
            raise ReviewError(HTTPStatus.BAD_REQUEST, exc.errors()[0]["msg"]) from exc
        lines = list(page.lines)
        lines[line_no - 1] = new
        page = page.model_copy(update={"lines": lines})
        (self.run_dir / f"{page_id}.json").write_text(page.model_dump_json(indent=2), encoding="utf-8")
        self.audit.write(page_id, "human_correction", line=line_no, field=field,
                         before=getattr(old, field), after=getattr(new, field))
        # The human looked at this line: a correction approves it. raw_text keeps what the page says.
        return self.approve(page_id, line_no)

    def set_page_date(self, page_id: str, value: str) -> dict:
        """The human reads the date the agent missed; undated lines take it too."""
        page = self.load(page_id)
        try:
            dated = LedgerPage.model_validate(json.loads(page.model_dump_json()) | {"page_date": value or None})
        except ValidationError as exc:
            raise ReviewError(HTTPStatus.BAD_REQUEST, exc.errors()[0]["msg"]) from exc
        lines = [l if l.date else l.model_copy(update={"date": dated.page_date}) for l in dated.lines]
        dated = dated.model_copy(update={"lines": lines})
        (self.run_dir / f"{page_id}.json").write_text(dated.model_dump_json(indent=2), encoding="utf-8")
        self.audit.write(page_id, "human_correction", line=None, field="page_date", before=page.page_date,
                         after=dated.page_date)
        return self.view(page_id)

    def approve(self, page_id: str, target: int) -> dict:
        page = self.load(page_id)
        if target != PAGE and not 1 <= target <= len(page.lines):
            raise ReviewError(HTTPStatus.NOT_FOUND, f"no line {target}")
        approvals = self.approvals()
        approvals[page_id] = sorted(set(approvals.get(page_id, [])) | {target})
        (self.run_dir / "approvals.json").write_text(json.dumps(approvals, indent=2), encoding="utf-8")
        self.audit.write(page_id, "human_approval", target="page" if target == PAGE else target)
        return self.view(page_id)

    def book(self, page_id: str) -> dict:
        page = self.load(page_id)
        if self._sync_factory is None:
            raise ReviewError(HTTPStatus.SERVICE_UNAVAILABLE, "Odoo is not configured (ODOO_* in .env)")
        sync: OdooSync = self._sync_factory(self.audit)
        approved = set(self.approvals().get(page_id, []))
        sync.apply(plan_page(page_id, page, approved=approved))
        return self.view(page_id)


def make_handler(store: ReviewStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep the console for errors only
            pass

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=HTTPStatus.OK) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode(), "application/json")

        def _route(self, method: str) -> None:
            parts = [unquote(p) for p in self.path.split("?")[0].strip("/").split("/") if p]
            try:
                if method == "GET" and not parts:
                    html = resources.files("griot").joinpath("review.html").read_bytes()
                    return self._send(HTTPStatus.OK, html, "text/html; charset=utf-8")
                if method == "GET" and parts == ["api", "pages"]:
                    return self._json(store.summary())
                if method == "GET" and len(parts) == 3 and parts[:2] == ["api", "pages"]:
                    return self._json(store.view(parts[2]))
                if method == "GET" and len(parts) == 2 and parts[0] == "photo":
                    path = store.image(parts[1])
                    return self._send(HTTPStatus.OK, path.read_bytes(),
                                      "image/png" if path.suffix == ".png" else "image/jpeg")
                if method == "POST" and len(parts) == 4 and parts[:2] == ["api", "pages"]:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    page_id, action = parts[2], parts[3]
                    if action == "correct":
                        return self._json(store.correct(page_id, int(body["line"]), body["field"], body.get("value")))
                    if action == "approve":
                        target = PAGE if body["target"] == "page" else int(body["target"])
                        return self._json(store.approve(page_id, target))
                    if action == "page_date":
                        return self._json(store.set_page_date(page_id, body.get("value", "")))
                    if action == "book":
                        return self._json(store.book(page_id))
                raise ReviewError(HTTPStatus.NOT_FOUND, "not found")
            except ReviewError as exc:
                self._json({"error": str(exc)}, exc.status)
            except Exception as exc:  # show Odoo or validation errors in the page instead of a dead request
                self._json({"error": f"{type(exc).__name__}: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="Output folder of griot.agent")
    parser.add_argument("--images", type=Path, default=Path("data/samples"), help="Folder with the original photos")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    def sync_factory(audit: AuditLog) -> OdooSync:
        from griot.odoo import OdooClient

        return OdooSync(OdooClient.from_env(), audit)

    store = ReviewStore(args.run_dir, args.images, sync_factory)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    print(f"Review page: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
