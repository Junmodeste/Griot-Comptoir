# Evaluation bench

- `ground_truth/<page>.json` — the expected `LedgerPage` for `data/samples/<page>.jpg`, checked by hand.
  Numbers are what is **written** on the page, even when the arithmetic is wrong (the verification layer flags those).
- `predictions/<run>/` — model outputs (git-ignored). One folder per model/config, e.g. `muse-spark-1.3`, `muse-spark-open-1026`.

```powershell
python -m griot.extract data\samples --out eval\predictions\muse-spark-1.3
python -m griot.evaluate --pred eval\predictions\muse-spark-1.3 --out eval\report_muse-spark-1.3.json
```

Metrics: line recall, spurious lines, and accuracy per field (`entry_type`, `product`, `quantity`, `unit`,
`unit_price`, `total`, `customer`, `date`). Lines are aligned greedily so one skipped line does not shift
the rest. Text is compared case- and accent-insensitively; numbers exactly. `_run.json` in each
prediction folder holds latency and token usage per page.

## Ground truth for the real pages

`real_page_*.json` were drafted from a first Muse Spark run, then every line was checked against the photo
(zoomed where needed). Reading errors were corrected (e.g. "Piment frais", "Balai traditionnel", dates 21/10
and 03/11, 650 not 600). Rules applied to every page:

- `quantity` and `unit_price` are null when they are not written (no inferred unit price);
- a "Total" line goes to `written_total`, never to `lines`;
- `product` is a short canonical name: lowercase, singular, spelled correctly, generic noun + variety or
  brand, no packaging or bracketed detail, null for expenses;
- `rotation` is the clockwise rotation the original photo needs (pages 04, 05 and 11 are sideways).

Still open for a human check: page 04 lines 3–4 ("Huile rouge 1/1D", "magne sec"), the purchase-or-sale
reading of pages 07–09, and page 07 line 1 ("Boiss. Kouadio").

`product` is scored by exact match after normalisation, so a defensible variant ("pain baguette" for
"pain") counts as wrong: read it as a lower bound. Odoo product matching absorbs these variants.

## Results (15 real pages, 103 lines, muse-spark-1.3)

| Run (2026-11-02) | Stage | Overall | product | quantity | unit_price | total | date |
|---|---|---|---|---|---|---|---|
| v1: extract + re-read | raw extraction | 0.933 | 0.71 | 0.97 | 0.95 | 0.99 | 0.88 |
| | after agent | 0.934 | 0.71 | 0.97 | 0.95 | 0.99 | 0.88 |
| v2: + grounding, page-date hint | raw extraction | 0.958 | 0.78 | 0.99 | 0.99 | 0.99 | 0.95 |
| | after agent | **0.961** | 0.79 | 0.99 | **1.00** | 0.99 | 0.95 |
| v3: + canonical products, auto-rotation | raw extraction | 0.949 | 0.84 | 0.98 | 0.98 | 0.99 | 0.83 |
| | after agent | 0.952 | **0.85** | 0.98 | **1.00** | 0.99 | 0.83 |

Scored against the final ground truth. `entry_type` 0.99–1.00 and `customer` 1.00 in every run.
v3 reads the sideways pages correctly ("citron vert", "balai traditionnel") but missed three page dates
that v2 read (21/10, 15/10 in a box, 03/11 on a sideways page): dates vary from run to run.

Line recall 1.00, no invented line, written page totals 15/15 in every run. Synthetic pages: 1.00.
Runs differ by a few points on the same prompt: compare runs, not single numbers.

### Review queue (v3)

A human answers **5 line questions + 19 page questions** instead of re-reading 103 lines. The page
questions are 13 page totals that do not match their lines (the volunteers were asked to make some
mistakes) and 6 pages without a date. All 4 lines whose error would change the books sit on a flagged
page; none of them is flagged on its own line (`booked_error_recall_line` = 0), which is the next thing
to improve.
