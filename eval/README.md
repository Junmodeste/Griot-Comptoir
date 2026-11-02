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

## Muse Spark 1.3 vs Muse Spark Open (3 Nov, same agent, same prompt, reasoning medium/high)

Muse Spark 1.3 on Meta Model API, Muse Spark Open (`meta-models/muse-spark-open`) on Baseten. 15 real pages,
103 lines; both runs in parallel. Prices: $1.25 in / $4.25 out per million tokens for both (Meta pricing page;
Baseten rate from a third-party snapshot of its pricing page, Baseten's site is not reachable from the build box).

| | Muse Spark 1.3 | Muse Spark Open |
|---|---|---|
| Line recall / invented lines | 1.00 / 0 | 0.90 / 9 |
| Overall field accuracy (after the agent) | **0.961** | 0.782 |
| total · quantity · unit price | 0.98 · 0.97 · 1.00 | 0.86 · 0.84 · 0.90 |
| date · entry type · product | 0.95 · 1.00 · 0.83 | 0.84 · 0.87 · 0.35 |
| Synthetic pages | 1.00 | 0.95 |
| Seconds per page (all calls) | 34 | **24** |
| Cost per page (all calls, re-reads included) | $0.037 | $0.034 |
| Lines with a booking error | 6 | 24 |
| ...of which reach a human (line or page flag) | 5 (83 %) | **23 (96 %)** |
| Human questions (lines + pages) | 9 + 18 | 29 + 17 |

What the open model gets wrong on handwriting: it books headings and "Total" lines as entries, keeps
packaging in product names ("Carton savon mousse"), misreads words ("Savon" for "Serpillière") and once read an
11-line page as a single line. The verification layer is what keeps it safe: 23 of its 24 booking errors are
stopped before Odoo, at the cost of three times more human questions.

The one Muse Spark 1.3 error that slipped through is instructive: on page 14 it read "3.200" as 2 200, and the
writer had also written a wrong page total, 1 000 below the real sum. The misread made the lines add up to the
written total, so no check fired. Two independent errors that cancel out are the blind spot of the sum check.

Raw-extraction scores before the agent: Muse Spark 1.3 0.961, Muse Spark Open 0.767 (the agent's straightening
and re-read add 1.5 points to the open model).
