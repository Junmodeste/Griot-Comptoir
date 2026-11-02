"""Ledger agent: extract -> verify -> targeted re-read -> verify -> review queue.

Usage:
    python -m griot.agent data/samples --out runs/2026-11-02

Each page writes <page>.json (final LedgerPage), extracted/<page>.json (raw model
output), <page>.verdict.json (checks, confidence, review queue) and appends every
decision to audit.jsonl.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pydantic import ValidationError

from griot.client import MetaClient
from griot.extract import IMAGE_SUFFIXES, ExtractionError, extract_page, image_to_data_url
from griot.schema import LedgerPage, json_schema_format
from griot.verify import PageVerdict, drop_ungrounded, verify_page

LINE_FIELDS = ("raw_text", "entry_type", "product", "quantity", "unit", "unit_price", "total", "customer", "date")

REREAD_PROMPT = """\
You already transcribed this handwritten ledger page. An automatic checker flagged the problems \
listed below. Look at the photo again, slowly, only where the problems are, and return the whole \
page again with the same lines in the same order.

- Fix a field only if you misread the photo (a digit, a word, a date). Copy raw_text again from \
the photo for every line you change.
- If the writer made the mistake (e.g. a wrong multiplication, a wrong page total), keep exactly \
what is written: a human will decide.
- A value that is not written on the line must be null, never inferred.
- Never add, drop, merge or reorder lines.

Problems:
{problems}

Your first transcription:
{page}
"""


class AuditLog:
    """Append-only JSONL: one event per decision, never rewritten."""

    def __init__(self, path: Path):
        self.path = path

    def write(self, page: str, event: str, **data) -> None:
        record = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "page": page,
                  "event": event, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def describe_problems(page: LedgerPage, verdict: PageVerdict) -> str:
    lines = [f"- page: {i.message}" for i in verdict.issues]
    for v in verdict.review_queue:
        for issue in v.issues:
            lines.append(f"- line {v.index + 1} ({page.lines[v.index].raw_text!r}): {issue.message}")
        if not v.issues:
            lines.append(f"- line {v.index + 1} ({page.lines[v.index].raw_text!r}): hard to read")
    return "\n".join(lines)


def needs_reread(verdict: PageVerdict) -> bool:
    return bool(verdict.review_queue or verdict.issues)


def reread_page(client: MetaClient, image: Path, page: LedgerPage, verdict: PageVerdict) -> LedgerPage:
    prompt = REREAD_PROMPT.format(problems=describe_problems(page, verdict), page=page.model_dump_json(indent=1))
    response = client.chat(
        [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_to_data_url(image)}},
        ]}],
        response_format=json_schema_format(LedgerPage),
        reasoning_effort="high",
    )
    raw = response.choices[0].message.content or ""
    try:
        return LedgerPage.model_validate_json(raw)
    except ValidationError as exc:
        raise ExtractionError(f"{image.name}: invalid re-read output: {exc}", raw) from exc


def field_changes(before: LedgerPage, after: LedgerPage) -> list[dict]:
    changes = [{"line": None, "field": f, "before": getattr(before, f), "after": getattr(after, f)}
               for f in ("page_date", "written_total") if getattr(before, f) != getattr(after, f)]
    for i, (b, a) in enumerate(zip(before.lines, after.lines)):
        changes += [{"line": i + 1, "field": f, "before": getattr(b, f), "after": getattr(a, f)}
                    for f in LINE_FIELDS if getattr(b, f) != getattr(a, f)]
    return changes


def _grounded(name: str, page: LedgerPage, audit: AuditLog) -> LedgerPage:
    page, dropped = drop_ungrounded(page)
    if dropped:
        audit.write(name, "ungrounded_dropped", changes=dropped)
    return page


@dataclass
class PageResult:
    extracted: LedgerPage  # raw model output, kept to measure what verification changed
    page: LedgerPage
    verdict: PageVerdict
    reread: bool
    changes: list[dict]


def process_page(client: MetaClient, image: Path, audit: AuditLog, *, work_dir: Path,
                 reference_date: dt.date | None = None, reread: bool = True) -> PageResult:
    name = image.stem
    first = extract_page(client, image, reference_date=reference_date)
    raw = first.page
    audit.write(name, "extracted", model=first.model, lines=len(first.page.lines), latency_s=first.latency_s,
                prompt_tokens=first.prompt_tokens, completion_tokens=first.completion_tokens,
                rotation=first.page.rotation)
    if first.page.rotation:
        # A sideways photo is read worse: straighten it and read it again.
        upright = work_dir / "upright" / f"{name}.jpg"
        upright.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(image) as img:
            img.rotate(-first.page.rotation, expand=True).convert("RGB").save(upright, quality=95)
        image = upright
        first = extract_page(client, image, reference_date=reference_date)
        audit.write(name, "rotated_and_extracted", rotation=raw.rotation, lines=len(first.page.lines),
                    latency_s=first.latency_s, prompt_tokens=first.prompt_tokens,
                    completion_tokens=first.completion_tokens)
    page = _grounded(name, first.page, audit)
    verdict = verify_page(page)
    audit.write(name, "verified", review=[v.index + 1 for v in verdict.review_queue],
                issues=describe_problems(page, verdict).splitlines())

    changes: list[dict] = []
    did_reread = False
    if reread and needs_reread(verdict):
        did_reread = True
        start = time.perf_counter()
        try:
            second = reread_page(client, image, page, verdict)
        except ExtractionError as exc:
            audit.write(name, "reread_rejected", reason=str(exc))
            second = None
        if second is not None and len(second.lines) != len(page.lines):
            audit.write(name, "reread_rejected", reason=f"line count changed {len(page.lines)} -> {len(second.lines)}")
        elif second is not None:
            changes = field_changes(page, second)
            page = _grounded(name, second, audit)
            verdict = verify_page(page)
            audit.write(name, "reread", latency_s=round(time.perf_counter() - start, 3), changes=changes,
                        review=[v.index + 1 for v in verdict.review_queue])

    audit.write(name, "ready", auto=len(verdict.lines) - len(verdict.review_queue),
                review=[v.index + 1 for v in verdict.review_queue], page_issues=[i.code for i in verdict.issues])
    return PageResult(raw, page, verdict, did_reread, changes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path, help="Image files or directories")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reference-date", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--no-reread", action="store_true", help="Skip the targeted re-read step")
    args = parser.parse_args(argv)

    images = [f for p in args.images for f in (sorted(p.iterdir()) if p.is_dir() else [p])
              if f.suffix.lower() in IMAGE_SUFFIXES]
    client = MetaClient.from_env()
    (args.out / "extracted").mkdir(parents=True, exist_ok=True)
    audit = AuditLog(args.out / "audit.jsonl")
    failures = 0
    for image in images:
        try:
            result = process_page(client, image, audit, work_dir=args.out, reference_date=args.reference_date,
                                  reread=not args.no_reread)
        except ExtractionError as exc:
            failures += 1
            audit.write(image.stem, "failed", reason=str(exc))
            print(f"FAIL {image.name}: {exc}", file=sys.stderr)
            continue
        (args.out / f"{image.stem}.json").write_text(result.page.model_dump_json(indent=2), encoding="utf-8")
        (args.out / "extracted" / f"{image.stem}.json").write_text(result.extracted.model_dump_json(indent=2),
                                                                   encoding="utf-8")
        (args.out / f"{image.stem}.verdict.json").write_text(
            json.dumps(result.verdict.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        queue = len(result.verdict.review_queue)
        print(f"{image.name}: {len(result.page.lines)} lines, {queue} to review"
              + (f", re-read changed {len(result.changes)} fields" if result.reread else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
