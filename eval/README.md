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
(zoomed where needed). Reading errors were corrected (e.g. "Piment frais", "Balai traditionnel", date 03/11,
650 not 600) and the transcription rules were applied: `quantity` and `unit_price` stay null when they are
not written (no inferred unit price), and a "Total" line goes to `written_total`, not to `lines`.

Because the draft came from the model, the scores below are an upper bound on interpretive fields
(`product` wording, `entry_type`). Still open for a human check:

- page 04 lines 3–4: "Huile rouge 1/1D" and "magne sec" are hard to read;
- pages 05, 07, 08, 09: shopping list or shop sales? (`entry_type` purchase vs sale);
- page 07 line 1: "Boiss. Kouadio (2 sucreries)" (product, or a customer named Kouadio?).

## Results

| Run | Pages | Lines | Recall | Overall | Notes |
|---|---|---|---|---|---|
| muse-spark-1.3, medium, 2026-11-02 | 15 real | 103 | 1.00 | 0.973 | unit_price 0.92, date 0.95, product 0.96 |
| muse-spark-1.3, medium, 2026-11-02 | 3 synthetic | 18 | 1.00 | 1.000 | |

Numbers are before the verification layer.
