# Griot Comptoir

AI agent that turns handwritten shop ledgers, shelf photos and voice notes from West African small traders into verified stock, sales and debt records synced to Odoo. Built with Meta's Muse Spark, SAM 3.1 and Muse Voice Transcribe.

> Meta Global AI Developer Hackathon 2026 — Indie track — theme *Build for Your World*.
> Status: work in progress (built 27 Oct – 4 Nov 2026).

## Why

In Abidjan, most small shops ("boutiques", market stalls) keep their accounts in a paper notebook: cash sales, credit given to neighbours, restocking. That notebook never reaches an ERP. Griot Comptoir reads it, checks it, and writes it into Odoo.

## How it works

```
photo of a notebook page
   │
   ├─ Muse Spark 1.3 (vision, JSON schema)        read every line verbatim + structured fields
   ├─ sideways photo? rotate and read again        the model reports the rotation it sees
   ├─ grounding                                    drop any quantity/price not written on the line
   ├─ verification (deterministic)                 qty × price = total, sum of lines = page total,
   │                                               FCFA multiples of 5, units, debts need a customer
   ├─ targeted re-read (Muse Spark, high effort)   only where a check failed; writer errors are kept
   ├─ review queue                                 a human answers only the doubtful lines / pages
   └─ Odoo sync (XML-RPC)                          purchase → receipt, sale → delivery,
                                                   credit → customer invoice, repayment → payment
every step → audit.jsonl (append-only)
```

## How Meta models are used

| Capability | Where | Why it matters here |
|---|---|---|
| Muse Spark 1.3 vision on phone photos | `extract.py` | Reads handwriting, French abbreviations and nouchi ("gbêlê" = credit) |
| Structured output (`json_schema`, constrained decoding) | `schema.py` | One pydantic schema drives the request and the validation; no free-text parsing |
| Reasoning effort as a dial | `extract.py` (medium), `agent.py` re-read (high) | Spend more reasoning only on the lines a check flagged |
| Self-reported photo orientation | `LedgerPage.rotation` | The agent straightens sideways photos and reads them again |
| Muse Spark Open (open weights, via Baseten) | `--provider baseten` | Same agent, open-weight model: compared on accuracy, cost and latency |

## Quickstart (Windows PowerShell)

```powershell
py -m venv .venv   # Python 3.10+
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # fill MODEL_API_KEY, and the ODOO_* lines to sync
pytest                                                   # offline tests
python scripts\check_connection.py                      # Meta Model API reachable?
python -m griot.agent data\samples --out runs\demo     # read + verify + review queue
python -m griot.evaluate --pred runs\demo               # accuracy vs ground truth
python -m griot.review runs\demo                        # review page on http://127.0.0.1:8000
python -m griot.sync runs\demo                          # or book from the command line
```

The review page shows the photo next to what the agent read, asks only the open questions (a wrong
calculation, a page total that does not add up, a missing date), lets the human correct a value or
approve, and books the page into Odoo. Every correction and approval goes to `audit.jsonl`.

`scripts\setup_odoo_demo.py` prepares an empty test database (XOF, Côte d'Ivoire). Never run it on a real company.

## Results so far

On 15 handwritten pages (103 lines, phone photos sent over WhatsApp), with Muse Spark 1.3: every line
found, none invented, 0.96 field accuracy, unit price 1.00 after grounding, about $0.04 per page for the whole
agent. A human answers a few line and page questions instead of re-reading 103 lines.

Same agent on the open-weight Muse Spark Open: faster (24 s vs 34 s per page) but weaker on handwriting
(0.78 field accuracy); the verification layer still stops 23 of its 24 booking errors before Odoo.
Details and caveats: [eval/README.md](eval/README.md).

## Repository layout

| Path | Content |
|---|---|
| `src/griot/client.py` | Meta Model API client (OpenAI-compatible), retries on 429/5xx/timeouts only |
| `src/griot/schema.py` | `LedgerPage` / `LedgerLine` schema, shared by the model request and the validation |
| `src/griot/extract.py` | Ledger photo → validated `LedgerPage` |
| `src/griot/verify.py` | Deterministic checks, confidence, review queue |
| `src/griot/agent.py` | The agent loop: extract, rotate, ground, verify, re-read |
| `src/griot/odoo.py`, `sync.py` | Odoo XML-RPC client and idempotent booking (`GRIOT/<page>/L<lines>` references) |
| `src/griot/audit.py` | Append-only JSONL audit log |
| `src/griot/review.py`, `review.html` | Local review page (French UI, Python standard library server) |
| `src/griot/evaluate.py` | Per-field accuracy and review-queue metrics |
| `data/samples/`, `eval/ground_truth/` | 15 handwritten + 3 synthetic pages and their ground truth |

## Known limits

- Units: kg/g/L/cl/ml use Odoo's own units (converted by Odoo); notebook containers (sac, carton, tas...)
  become units of their own. A product keeps the unit of its first booking, and a later line in a unit
  that cannot convert is held for review (6 of 103 lines on the sample pages, all genuine ambiguities).
- A line with no written quantity ("Ail : 100F") is booked as 1 unit; there is no opening stock, so a sale
  before any purchase makes stock negative.
- Product names are matched to Odoo by exact (case-insensitive) name; a new spelling creates a new product.
- No VAT: small informal shops sell taxes included.
- XML-RPC (required for this project) is deprecated in Odoo 19 and scheduled for removal in Odoo 22 (server log); `odoo.py` is the only place to change for the JSON-2 API.

## Data and privacy

No real customer data is stored in this repository. Sample pages are synthetic; real pages used for evaluation are anonymised before being added.

## License

TBD
