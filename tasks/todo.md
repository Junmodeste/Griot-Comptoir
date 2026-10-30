# Griot Comptoir — TODO

Deadline: submit 4 Nov 2026 before 18:00 Abidjan (hard limit 5 Nov 02:00 Abidjan).

## API facts (verified 2026-10-30 in meta-models/meta-model-cookbook notebooks 01/04/07/09, then in dev.meta.ai/docs: models, quickstart, structured-output, image-understanding)

- OpenAI-compatible: `OpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])`.
- Model ID: `muse-spark-1.3` (reasoning model, `reasoning_effort`: minimal/low/medium/high).
- Images: `{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}`, **user messages only** (system/developer → HTTP 400). jpeg/png/gif/webp, ≤50 MB inline.
- Structured output: `response_format={"type": "json_schema", "json_schema": {"name", "schema"}}`, constrained decoding; recursive `$ref` rejected.
- Unsupported params → 400: `stop`, `n>1`, `logit_bias`. Retry only 429 / 5xx / timeouts.
- Muse Spark Open (Baseten): model `meta-models/muse-spark-open-1026` — base URL still to verify.
- Other hosted models: `muse-wonder-1.0`, `sam-3.1` (Responses API), `muse-voice-transcribe-1.0` (`POST /v1/asr/transcribe`). `-contributor` model IDs are cheaper but content may train Meta models: do not use with real shop data.
- api.meta.ai is still blocked from the cloud session (see lessons.md).

## Day 1 — 30 Oct

- [x] 0. Check branch = `Dev`, read cookbook, write this plan
- [x] 1. Scaffold: `src/griot/`, `tests/`, `data/samples/`, `eval/`, README, `.gitignore`, `.env.example`, `pyproject.toml`, `requirements.txt`
- [x] 2. Meta Model API client (`src/griot/client.py`): env config, retry on transient errors, `ping()` + `scripts/check_connection.py`
- [x] 3. Pipeline image → validated JSON: pydantic schema (`schema.py`) + `extract.py` (Muse Spark vision + json_schema) + CLI
- [x] 4. Synthetic anonymised ledger pages in `data/samples/` (generator + ground truth); offline tests pass
- [ ] 4b. **Live run** of the extraction on `data/samples/` — needs `MODEL_API_KEY` on the Windows machine (API blocked in cloud session)
- [x] 5. Eval bench skeleton: ground truth JSON, per-field accuracy with line alignment (`evaluate.py`, `eval/`)

## Next (31 Oct →)

- [ ] Verification layer: arithmetic (qty × price = total), units, per-line confidence, review queue, audit log
- [ ] Odoo XML-RPC sync + audit log
- [ ] Collect 15–20 real anonymised pages + ground truth
- [ ] SAM 3.1 shelf counting
- [ ] Muse Spark vs Muse Spark Open comparison (accuracy, cost, latency)
- [ ] Voice note → action (Muse Voice Transcribe)
- [ ] Demo video (3 min)

## Skills used

- None so far for day 1: the scaffold and client were small enough to write directly; `engineering:testing-strategy` / `code-review` planned for the verification layer.
