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
