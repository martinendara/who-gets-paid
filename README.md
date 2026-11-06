# Who Gets Paid When AI Answers in Spanish?

A public pilot (Mozilla Fellowship 2027) measuring whether AI engines cite the
original outlet when they reuse its exclusive. Spanish- and English-language
answers to the same facts are compared.

The repository contains a minimal runner that queries official APIs, normalizes
citations, and stores raw answers for classification. Private story URLs and
outlet names live in `data/private/`, which is gitignored.

## What it does
- Reads queries from a CSV (`query_id,text`).
- Sends each query verbatim to each engine (no wrapper or system prompt).
- Saves one JSONL record per pair with citations, retrieved results, search
  count, token usage, and cost.
- Resumes without paying twice; stops above `PILOT_CEILING_USD`.

## How to run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
set -a && source .env && set +a
echo ${#GEMINI_API_KEY} ${#ANTHROPIC_API_KEY}   # lengths only
python -m src.run --file data/smoke.csv --engines gemini,claude --run-id smoke
```

If TLS fails with certificate errors (Python without a CA bundle), run
with `SSL_CERT_FILE=/etc/ssl/cert.pem` or the path your system uses.
Output: `runs/<run_id>/answers.jsonl` (gitignored). Engines: Gemini
`gemini-3.5-flash-lite` with Google Search via `generateContent`; Claude
`claude-sonnet-5-5` with `web_search_20250305`. OpenAI and Perplexity are
planned for IMPL-02.

## Limitations
Small, non-probabilistic sample: descriptive, not causal. Search results and
model behavior change over time; each run is timestamped. Proprietary systems
can change citation formats. No private data, keys, or story URLs are committed.

## License
Code: MIT. Data and documentation: CC BY 4.0. See `LICENSE` and `LICENSE-DATA`.
