"""Render synthetic, fully fictional ledger pages with their ground truth.

No real customer data: names, products and amounts below are invented.
Real anonymised pages go next to them later, with hand-written ground truth.

Usage:
    python scripts/make_synthetic_pages.py --font path/to/Caveat.ttf

A handwriting font makes the pages closer to reality (Caveat, SIL OFL:
https://github.com/google/fonts/tree/main/ofl/caveat). Without --font, a
system font is used.
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from griot.schema import LedgerLine, LedgerPage

ROOT = Path(__file__).resolve().parent.parent

# (written text, entry_type, product, qty, unit, unit_price, total, customer, date)
PAGES = {
    "synthetic_page_01": ("2026-10-12", [
        ("Riz parfumé 2 sacs x 18.500 = 37.000", "sale", "riz parfumé", 2, "sac", 18500, 37000, None, "2026-10-12"),
        ("Huile 3 L x 1.300 = 3.900", "sale", "huile", 3, "L", 1300, 3900, None, "2026-10-12"),
        ("Koffi doit : sucre 2 paquets x 650 = 1.300", "debt", "sucre", 2, "paquet", 650, 1300, "Koffi", "2026-10-12"),
        ("Savon 5 x 250 = 1.250", "sale", "savon", 5, None, 250, 1250, None, "2026-10-12"),
        ("Tantie Adjo a payé 5.000", "repayment", None, None, None, None, 5000, "Tantie Adjo", "2026-10-12"),
        ("Lait concentré 4 boîtes x 450 = 2.000", "sale", "lait concentré", 4, "boîte", 450, 2000, None, "2026-10-12"),
    ]),
    "synthetic_page_02": ("2026-10-13", [
        ("Achat grossiste : 10 sacs riz x 17.000 = 170.000", "purchase", "riz", 10, "sac", 17000, 170000, None, "2026-10-13"),
        ("Attiéké 6 tas x 200 = 1.200", "sale", "attiéké", 6, "tas", 200, 1200, None, "2026-10-13"),
        ("Maman Awa crédit : tomate 3 boîtes x 300 = 900", "debt", "tomate", 3, "boîte", 300, 900, "Maman Awa", "2026-10-13"),
        ("Cube Maggi 20 x 25 = 500", "sale", "cube maggi", 20, None, 25, 500, None, "2026-10-13"),
        ("Pétrole 2 L x 700 = 1.400", "sale", "pétrole", 2, "L", 700, 1400, None, "2026-10-13"),
    ]),
    "synthetic_page_03": ("2026-10-14", [
        ("Gaz 1 bouteille x 5.500 = 5.500", "sale", "gaz", 1, "bouteille", 5500, 5500, None, "2026-10-14"),
        ("Yao gbêlê : pain 4 x 150 = 600", "debt", "pain", 4, None, 150, 600, "Yao", "2026-10-14"),
        ("Huile 1 bidon 5 L x 6.000 = 6.000", "sale", "huile", 1, "bidon", 6000, 6000, None, "2026-10-14"),
        ("Koffi a payé 1.300", "repayment", None, None, None, None, 1300, "Koffi", "2026-10-14"),
        ("Spaghetti 8 paquets x 300 = 2.400", "sale", "spaghetti", 8, "paquet", 300, 2400, None, "2026-10-14"),
        ("Sucre 3 kg x 700 = 2.200", "sale", "sucre", 3, "kg", 700, 2200, None, "2026-10-14"),
        ("Achat : 2 cartons savon x 9.000 = 18.000", "purchase", "savon", 2, "carton", 9000, 18000, None, "2026-10-14"),
    ]),
}
# Lines 6 of page 01 (4 x 450 written as 2.000) and 6 of page 03 (3 x 700 written as 2.200)
# carry deliberate arithmetic errors for the verification layer to catch.


def build_page(page_date: str, rows: list[tuple]) -> LedgerPage:
    lines = [
        LedgerLine(
            raw_text=text, entry_type=et, product=prod, quantity=qty, unit=unit,
            unit_price=price, total=total, customer=cust, date=day, legibility="high",
        )
        for text, et, prod, qty, unit, price, total, cust, day in rows
    ]
    return LedgerPage(page_date=page_date, currency="XOF", lines=lines, notes=None)


def render(page: LedgerPage, font_path: str | None, seed: int) -> Image.Image:
    rng = random.Random(seed)
    width, height, line_gap = 1240, 1754, 110
    img = Image.new("RGB", (width, height), (246, 242, 228))
    draw = ImageDraw.Draw(img)
    for y in range(220, height - 60, line_gap):
        draw.line([(40, y), (width - 40, y)], fill=(170, 190, 220), width=2)
    draw.line([(150, 0), (150, height)], fill=(220, 120, 120), width=3)

    size = 62 if font_path else 44
    font = ImageFont.truetype(font_path or "DejaVuSans-Oblique.ttf", size)
    ink = (25, 35, 110)
    draw.text((180, 110), page.page_date.strftime("%d/%m"), font=font, fill=ink)
    for i, line in enumerate(page.lines):
        x = 170 + rng.randint(-8, 12)
        y = 220 + (i + 1) * line_gap - size - 14 + rng.randint(-6, 4)
        draw.text((x, y), line.raw_text, font=font, fill=ink)

    img = img.rotate(rng.uniform(-1.5, 1.5), expand=False, fillcolor=(90, 80, 70))
    return img.filter(ImageFilter.GaussianBlur(0.6))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--font", help="Path to a handwriting .ttf font")
    args = parser.parse_args()

    samples, truth = ROOT / "data" / "samples", ROOT / "eval" / "ground_truth"
    samples.mkdir(parents=True, exist_ok=True)
    truth.mkdir(parents=True, exist_ok=True)
    for seed, (name, (page_date, rows)) in enumerate(PAGES.items()):
        page = build_page(page_date, rows)
        render(page, args.font, seed).save(samples / f"{name}.jpg", quality=85)
        (truth / f"{name}.json").write_text(page.model_dump_json(indent=2), encoding="utf-8")
        print(f"{name}: {len(page.lines)} lines")


if __name__ == "__main__":
    main()
