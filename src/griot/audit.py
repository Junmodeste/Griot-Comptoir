"""Append-only audit log shared by the agent and the Odoo sync."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class AuditLog:
    """Append-only JSONL: one event per decision or write, never rewritten."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, page: str, event: str, **data) -> None:
        record = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "page": page,
                  "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
