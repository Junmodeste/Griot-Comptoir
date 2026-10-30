# Griot Comptoir

AI agent that turns handwritten shop ledgers, shelf photos and voice notes from West African small traders into verified stock, sales and debt records synced to Odoo. Built with Meta's Muse Spark, SAM 3.1 and Muse Voice Transcribe.

> Meta Global AI Developer Hackathon 2026 — Indie track — theme *Build for Your World*.
> Status: work in progress (built 27 Oct – 4 Nov 2026).

## Why

In Abidjan, most small shops ("boutiques", market stalls) keep their accounts in a paper notebook: cash sales, credit given to neighbours, restocking. That notebook never reaches an ERP. Griot Comptoir reads it, checks it, and writes it into Odoo.

## How it works

```
ledger photo ──► Muse Spark (vision + JSON schema) ──► LedgerPage (pydantic)
                                                        │
                                       verification (arithmetic, units, confidence)  [planned]
                                                        │
                                        human review of doubtful lines only          [planned]
                                                        │
                                          Odoo XML-RPC sync + audit log               [planned]
```

## Quickstart (Windows PowerShell)

```powershell
py -m venv .venv   # Python 3.10+
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then put your Meta Model API key in MODEL_API_KEY
python scripts\check_connection.py
python -m griot.extract data\samples --out eval\predictions\muse-spark-1.3
python -m griot.evaluate --pred eval\predictions\muse-spark-1.3
pytest
```

## Repository layout

| Path | Content |
|---|---|
| `src/griot/client.py` | Meta Model API client (OpenAI-compatible), retries on 429/5xx/timeouts only |
| `src/griot/schema.py` | `LedgerPage` / `LedgerLine` schema, shared by the model request and the validation |
| `src/griot/extract.py` | Ledger photo → validated `LedgerPage` (CLI) |
| `src/griot/evaluate.py` | Per-field accuracy against ground truth (CLI) |
| `data/samples/` | Synthetic, fully fictional ledger pages |
| `eval/ground_truth/` | Hand-checked JSON for each sample page |
| `scripts/` | Connection check, synthetic page generator |
| `tasks/` | Working plan and lessons learned |

## Data and privacy

No real customer data is stored in this repository. Sample pages are synthetic; real pages used for evaluation are anonymised before being added.

## License

TBD
