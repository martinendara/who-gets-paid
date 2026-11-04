# Method

**Preregistered on 2026-11-04. Any change after the first real query is logged under Amendments with date.**

## 1. Research question

When an AI engine answers a question in Spanish about a fact that a Spanish-language outlet published first, does the engine cite the original outlet? And how does that compare with the same fact asked in English?

## 2. Hypothesis

In Spanish-language answers, the original outlet is cited less often than in English-language answers to the same facts; part of the use is displaced to English-language sources.

The hypothesis is directional but does not fix a magnitude. The pilot reports descriptive rates and their denominators; it is not powered for a causal claim.

## 3. Sampling frame

- **Outlets:** 10 Spanish-language outlets, five from Argentina and five from Chile, balanced across large and small outlets (`outlet_size`). Outlet names are kept only in `data/private/`; the public file uses anonymized IDs (`AR-L1`, `AR-S3`, `CL-S1`, …).
- **Stories:** 40 stories, four per outlet. Each story is a factual news story whose central fact is stated in `fact_key`.
- **Origin criteria:** a story enters the sample only if it meets at least one of:
  - `first_published`: the outlet published the fact before other outlets;
  - `own_investigation`: the outlet produced the investigation or original reporting;
  - `attributed_exclusive`: the story is explicitly presented as an exclusive, with attribution.
- **Origin evidence:** each story records a URL or a description supporting the criterion in `origin_evidence` (private version keeps the URL).
- **Selection:** the sample is purposive, not probabilistic. It is fixed in `stories.csv` before the first real query. No story is added or removed after seeing model outputs.
- **Private/public split:** `data/private/` holds URLs and real outlet names and is never committed. The public `data/stories.csv` is anonymized.

## 4. Question design

Each story receives four queries:

- two in Spanish (`lang=es`) and two in English (`lang=en`);
- in each language, one **direct** query that asks for the central fact, and one **open** query that asks about the topic without stating the fact;
- no query names the outlet, links to the story, or contains a wrapper instruction;
- the English queries ask about the same fact, not a different fact.

The exact text of the four queries is fixed in `queries.csv` before the first real query. The `type` field distinguishes `direct`, `open`, and `user`; the preregistered primary comparison uses `direct` and `open`. `user`-type phrasing is exploratory and does not enter the primary analysis unless added by amendment before the first real query.

Queries are sent verbatim (D17): no system prompt, no prefix, no rephrasing by the runner.

## 5. Engines and models

| Engine | API model | Search tool | SDK | Price verified |
|---|---|---|---|---|
| Gemini | `gemini-3.5-flash-lite` | `google_search` via `generateContent` | `google-genai==2.34.0` | 2026-11-04 |
| Claude | `claude-sonnet-5-5` | `web_search_20250305` | `anthropic==2.4.0` | 2026-11-04 |
| OpenAI | to be fixed in IMPL-02, before the first real query | to be fixed in IMPL-02 | — | — |
| Perplexity | to be fixed in IMPL-02, before the first real query | to be fixed in IMPL-02 | — | — |

- **Gemini pricing:** USD 0.30 input / USD 2.50 output per 1M tokens. Google Search is free up to 5,000 requests per month on the paid tier, then USD 14 per 1,000 requests. The project has billing enabled; only tokens are charged during the pilot.
- **Claude pricing:** USD 2.00 input / USD 10.00 output per 1M tokens. Web search is USD 10 per 1,000 searches.
- **Execution:** each `(query_id, engine)` pair is queried once per run and stored as one JSONL record. The record includes the requested model, the model version reported by the API, the answer text, citations, retrieved results (Claude only), search count, token usage, cost, and any error.
- **Resume:** a pair already present in `runs/<run_id>/answers.jsonl` is skipped and not paid for again.
- **Ceiling:** the runner stops when cumulative known cost exceeds `PILOT_CEILING_USD` (default USD 20).
- **Nulls:** a missing cost, denominator, or usage datum is stored as `null`, never as 0.

## 6. Classification

The classifier makes one judgment per answer: whether the central fact is present and correct.

- `fact_present = yes`: the answer states the central fact correctly.
- `fact_present = no`: the answer does not state the central fact.
- `fact_present = wrong`: the answer states the central fact incorrectly.

The classifier is an LLM that only returns `yes`, `no`, or `wrong`. It is not one of the audited engines. The classifier model and the prompt version are recorded in `labels.csv`; the prompt lives in `prompts/` and every change creates a new version. The classifier is fixed in IMPL-02 before classification starts.

Codes A–E are assigned by code, not by the classifier:

| Code | Condition |
|---|---|
| A | `fact_present=yes` and the original outlet's host appears among the citations |
| B | `fact_present=yes`, original outlet not cited, and at least one cited host is classified as Spanish-language (`es`) in `host_language.csv` |
| C | `fact_present=yes`, original outlet not cited, no Spanish-language host cited, and at least one cited host is classified as English-language (`en`) |
| D | `fact_present=yes` and no cited host is classified (no citations, or only `other` hosts) |
| E | `fact_present=no` or `fact_present=wrong` |

If the original outlet is cited alongside others, the answer is A. If the fact is absent or wrong, the answer is E regardless of citations. Hosts are canonicalized before matching: lowercase, no `www.`, no port (`canonical_host`).

## 7. Metrics

All metrics are ratios with an explicit denominator. If the denominator is 0, the metric is `null`, not 0. Every ratio is bounded by 0 and 1; a value outside that range is a bug.

Let `N = A + B + C + D + E` (all valid answers) and `P = A + B + C + D` (answers with the fact present and correct).

- **Fact-presence rate:** `P / N`. Denominator: all valid answers.
- **Original-attribution rate:** `A / P`. Denominator: answers with the fact present and correct.
- **Spanish-source displacement rate:** `B / P`.
- **English-source displacement rate:** `C / P`.
- **No-classified-citation rate:** `D / P`.
- **Attribution gap (Spanish minus English):**
  `A_es / P_es − A_en / P_en`.
  Denominator: answers with the fact present and correct in each language. If either language denominator is 0, the gap is `null`.
- **Per-engine and per-country breakdowns:** the same ratios with the corresponding denominators. Report counts alongside every rate.

**Costs:** total known cost is the sum of non-null `cost_usd` values. If any record has a null cost, report the number of records with unknown cost and do not present the sum as complete.

## 8. Human validation

- A random 20% sample of labeled `(query_id, engine)` pairs is validated by a human coder.
- Sampling is stratified by engine and, where possible, by answer language. The sampling frame is the set of answers with a valid record.
- The human coder is blind to the classifier's label and to the engine name. The coder sees the query, the answer text, the citations, and the host-language table, and assigns `fact_present` (yes/no/wrong) and the A–E code.
- Agreement is the proportion of exact matches between the classifier and the human coder on `fact_present`.
- Cohen's kappa is computed for the three-category `fact_present` label, unweighted. If the sample contains only one observed category, kappa is `null` rather than 0.
- Report the number of validated answers, agreement, and kappa. The validation sample is stored in `labels.csv` (`human_code`; empty for records outside the sample).

## 9. Limitations

- The sample is purposive and small (10 outlets, 40 stories, 4 queries per story); results are descriptive and not generalizable to all Spanish-language media.
- Engines are proprietary and change over time. Each record stores `run_at` and `model_version`; a later run is a new observation, not a replication of the same engine state.
- Search results vary with location, time, and personalization. The runner does not control search location beyond API defaults.
- Citation presence is not the same as legal attribution, licensing, or payment. The pilot measures what the engine displays, not the underlying commercial arrangement.
- The classifier is an LLM; human validation covers only 20% of answers. Human coding itself may be error-prone.
- Host language is assigned manually in `host_language.csv`; bilingual or regional hosts may be misclassified.
- **Technical limitation (Interactions API):** Google's current documentation foregrounds the Interactions API, whose citations are `url_citation` annotations. This pilot uses `generateContent` because it still exposes `grounding_metadata.grounding_chunks` and `web_search_queries` (D16). If that surface is removed, the Gemini adapter must be amended before the next real run.
- The pilot does not measure whether the original outlet was paid, only whether it was cited.

## 10. Amendments

None.
