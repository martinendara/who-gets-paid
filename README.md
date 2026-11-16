# Who Gets Paid When AI Answers in Spanish?

An open instrument that measures, story by story, whether AI answer engines
credit the outlet that broke a story when they repeat its exclusive, or give
the credit to someone else.

The repository contains a minimal runner that queries official APIs, normalizes
citations, and stores raw answers for classification. Private story URLs and
outlet names live in `data/private/`, which is gitignored.

## Results (pilot, November 2026)

One run on 9 November 2026: 29 original stories from 10 leading news outlets in Argentina and Chile, four questions per story, four engines (Gemini, ChatGPT, Claude, Perplexity) through their official APIs. 464 answers in total.

On direct questions (232 answers), 208 contained the story's key fact. Who got the credit:

| Outcome | Answers |
| --- | --- |
| The original outlet was cited | 124 of 208 (0.596; 95% interval 0.510–0.686) |
| Only other Spanish-language sources were cited | 68 of 208 (0.327; 95% interval 0.257–0.401) |
| Only English-language sources were cited | 15 of 208 |
| No source was cited | 1 of 208 |

**In 84 of 208 answers the engine used the exclusive and did not credit the outlet that reported it.**

- By engine (original outlet cited / answers with the fact): Gemini 24/54, ChatGPT 31/56, Claude 30/51, Perplexity 39/47. Perplexity's API lists its sources without marking which ones support the answer, so every listed source counts as a citation and its figure is not directly comparable.
- Found but not cited: in 15 of 56 ChatGPT answers and 3 of 51 Claude answers that contained the fact, the outlet's site was among the pages the engine retrieved and was not cited.
- Exact story: among answers that contained the fact, the exact story URL was cited in 25 of 56 (ChatGPT), 26 of 51 (Claude) and 28 of 47 (Perplexity).
- Seven days later, a repeat of 29 questions on the four engines (116 answers) gave the same code in 90 of 116 pairs; 34 of 44 on direct questions.
- Validation: two independent classifiers (agreement 0.886, Cohen's kappa 0.770), with blind human review of their 53 disagreements and of 30 agreement checks.
- Cost of the main run including classification: USD 5.75, about USD 0.012 per answer.

Intervals come from a bootstrap that resamples stories (2,000 resamples). Results on open-ended questions are exploratory; see `METHOD.md` §10. Full figures: `results/metrics_main-2026-11-09.json` and `results/volatility_repeat-2026-11-16.json`. Outlets are not named; each one receives its own results privately.

**En español.** Piloto del 9 de noviembre de 2026: 29 notas originales de 10 medios de Argentina y Chile, consultadas en cuatro motores de respuesta con IA. En preguntas directas, 208 respuestas contenían el dato de la nota; en 84 de ellas el motor no citó al medio que lo publicó, y en 68 el crédito fue para otro sitio en español.

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
python -m src.run --file data/smoke.csv --engines gemini,claude --run-id cut-smoke
```

Runs whose id does not start with `cut-` require a complete sample sheet
(`python -m src.validate_sample`).

If TLS fails with certificate errors (Python without a CA bundle), run
with `SSL_CERT_FILE=/etc/ssl/cert.pem` or the path your system uses.

Output: `runs/<run_id>/answers.jsonl` (gitignored). Engines: Gemini
`gemini-3.5-flash-lite` with Google Search via `generateContent`; Claude
`claude-haiku-5-5` with `web_search_20250305`; OpenAI `gpt-6-luna` with
`web_search` via the Responses API; Perplexity `perplexity/sonar` via the
Agent API.

## Limitations
Small, non-probabilistic sample: descriptive, not causal. Search results and
model behavior change over time; each run is timestamped. Proprietary systems
can change citation formats. No private data, keys, or story URLs are committed.

## License
Code: MIT. Data and documentation: CC BY 4.0. See `LICENSE` and `LICENSE-DATA`.
