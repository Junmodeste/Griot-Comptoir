"""Ledger page photo -> validated LedgerPage, using Muse Spark vision.

Usage:
    python -m griot.extract data/samples --out eval/predictions/muse-spark-1.3
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from griot.client import MetaClient
from griot.schema import LedgerPage, json_schema_format

SYSTEM_PROMPT = """\
You read photos of handwritten ledger notebooks ("cahiers") kept by small shopkeepers \
in Côte d'Ivoire. Pages are in French, often with abbreviations and nouchi \
(e.g. "Bra" = brazzaville / small bag, "gbêlê" = credit, "tas" = heap), and amounts are \
in FCFA (XOF), often followed by "F". A dot, comma or space followed by exactly three digits \
is a thousands separator ("2.500", "5,000", "2 500" = 2500); otherwise a comma is a decimal \
mark ("1,5 kg" = 1.5). "2,5 mil" = 2500.

Rules:
- One output line per ledger line, in reading order. Never merge or invent lines.
- Copy each line verbatim into raw_text before interpreting it.
- Transcribe numbers exactly as written; do NOT correct arithmetic. If quantity x unit_price \
does not equal the written total, keep what is written: a later verification step checks it.
- Use null for anything not written or not readable. Never guess a customer name.
- entry_type: debt when the line marks credit ("crédit", "doit", "gbêlê", "pas payé", \
a customer name with no payment), repayment when a customer pays back, purchase for \
restocking or market shopping, expense for money out that buys no goods (transport, \
taxes, fees), sale for cash sales, other for anything else (e.g. change given back).
- A "Total" line at the bottom is not a ledger line: put its amount in written_total. \
Headings ("Liste du marché", "Liste 3") go in notes.
- Dates: ISO YYYY-MM-DD. The page date is usually at the top, sometimes in a box or after \
"Date", "Marché du" or a heading; read day and month carefully. A line without its own date \
takes the date written above it (usually the page date). If the year is missing, use the reference year given below.
- unit: lowercase singular ("2 sacs" -> "sac"); null when no unit is written.
- quantity and unit_price: only when written on the line. "Carton savon : 4.800" has quantity null \
and unit_price null, not 1 and 4800.
- legibility: low if you had to guess any field, medium if one field was hard, else high.
"""


class ExtractionError(RuntimeError):
    """The model answer did not validate; raw_output keeps it for the audit log."""

    def __init__(self, message: str, raw_output: str):
        super().__init__(message)
        self.raw_output = raw_output


@dataclass
class Extraction:
    page: LedgerPage
    model: str
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None


# Explicit table: on Windows, mimetypes reads the registry and may answer image/pjpeg, which the API rejects.
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
               ".gif": "image/gif"}
IMAGE_SUFFIXES = set(IMAGE_TYPES)


def image_to_data_url(path: Path) -> str:
    mime = IMAGE_TYPES.get(path.suffix.lower())
    if mime is None:
        raise ValueError(f"Unsupported image type for {path}")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def extract_page(
    client: MetaClient,
    image_path: Path,
    *,
    reference_date: dt.date | None = None,
    reasoning_effort: str = "medium",
) -> Extraction:
    reference_date = reference_date or dt.date.today()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            # Images are only accepted in user messages.
            "content": [
                {"type": "text", "text": f"Reference date: {reference_date.isoformat()}. Extract this page."},
                {"type": "image_url", "image_url": {"url": image_to_data_url(image_path)}},
            ],
        },
    ]
    start = time.perf_counter()
    response = client.chat(
        messages,
        response_format=json_schema_format(LedgerPage),
        reasoning_effort=reasoning_effort,
    )
    latency = time.perf_counter() - start

    raw = response.choices[0].message.content or ""
    try:
        page = LedgerPage.model_validate_json(raw)
    except ValidationError as exc:
        raise ExtractionError(f"{image_path.name}: invalid model output: {exc}", raw) from exc

    usage = response.usage
    return Extraction(
        page=page,
        model=response.model or client.config.model,
        latency_s=round(latency, 3),
        prompt_tokens=usage.prompt_tokens if usage else None,
        completion_tokens=usage.completion_tokens if usage else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("images", nargs="+", type=Path, help="Image files or directories")
    parser.add_argument("--out", type=Path, required=True, help="Directory for <image>.json predictions")
    parser.add_argument("--reference-date", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--reasoning-effort", default="medium", choices=["minimal", "low", "medium", "high"])
    parser.add_argument("--provider", default="meta", choices=["meta", "baseten"])
    args = parser.parse_args(argv)

    # Directories are expanded here because PowerShell does not expand *.jpg.
    images = [
        f for p in args.images
        for f in (sorted(p.iterdir()) if p.is_dir() else [p])
        if f.suffix.lower() in IMAGE_SUFFIXES
    ]
    client = MetaClient.from_env(args.provider)
    args.out.mkdir(parents=True, exist_ok=True)
    run, failures = [], 0
    for image in images:
        try:
            result = extract_page(
                client, image, reference_date=args.reference_date, reasoning_effort=args.reasoning_effort
            )
        except ExtractionError as exc:
            failures += 1
            print(f"FAIL {image.name}: {exc}", file=sys.stderr)
            (args.out / f"{image.stem}.raw.txt").write_text(exc.raw_output, encoding="utf-8")
            continue
        (args.out / f"{image.stem}.json").write_text(result.page.model_dump_json(indent=2), encoding="utf-8")
        meta = {k: v for k, v in asdict(result).items() if k != "page"}
        run.append({"image": image.name, "lines": len(result.page.lines), **meta})
        print(f"OK   {image.name}: {len(result.page.lines)} lines, {result.latency_s}s, "
              f"tokens in/out {result.prompt_tokens}/{result.completion_tokens}")

    (args.out / "_run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
