# Method

**Preregistered on 2026-11-04. Any change after the first real query is logged under Amendments with date.**

## 1. Research question

When an AI engine answers a question in Spanish about a fact that a Spanish-language outlet published first, does the engine cite the original outlet? And how does that compare with the same fact asked in English?

## 2. Hypothesis

In Spanish-language answers, the original outlet is cited less often than in English-language answers to the same facts; part of the use is displaced to English-language sources.

The hypothesis is directional but does not fix a magnitude. The pilot reports descriptive rates and their denominators; it is not powered for a causal claim.

## 3. Sampling frame

- **Outlets:** the five most-used Spanish-language written or digital-first news brands in Argentina and the five in Chile (broadcast-led brands excluded), listed in `data/private/outlets.csv` with the source used to rank them.
- **Stories:** each story is a scoop that originated in the outlet in its home country and was later reproduced by at least one English-language outlet. The English replica's URL is recorded (`english_replica_url`, private). Because every story has an English replica by design, English-source displacement (C) is measured conditional on an English alternative existing; the pilot does not estimate how often such replicas exist. The sample is 40 stories, four per outlet, and each story's central fact is stated in `fact_key`.
- **Origin criteria:** a story enters the sample only if it meets at least one of:
  - `first_published`: the outlet published the fact before other outlets;
  - `own_investigation`: the outlet produced the investigation or original reporting;
  - `attributed_exclusive`: the story is explicitly presented as an exclusive, with attribution.
- **Origin evidence:** each story records a URL or a description supporting the criterion in `origin_evidence` (private version keeps the URL).
- **Selection:** the sample is purposive, not probabilistic. It is fixed in `stories.csv` before the first real query. No story is added or removed after seeing model outputs.
- **Private/public split:** `data/private/` holds URLs and real outlet names and is never committed. The public `data/stories.csv` is anonymized.
- **Feasibility run (D33):** before the first query of the sample, a technical run with 5 stories that are NOT part of the 40-story sample checks that all four engines and the parser work. Its `run_id` starts with `cut-`, and it is excluded from every metric.

## 4. Question design

Each story receives four queries in a 2×2 design by language and phrasing:

| `type` | Spanish (`lang=es`) | English (`lang=en`) |
|---|---|---|
| `direct` | asks for the central fact | asks for the same fact |
| `open` | asks about the topic without stating the fact | asks about the same topic without stating the fact |

- No query names the outlet, links to the story, or contains a wrapper instruction.
- The `type` field is `direct` or `open`; no other query type is used.
- The exact text of the four queries is fixed in `queries.csv` before the first real query.

Queries are sent verbatim (D17): no system prompt, no prefix, no rephrasing by the runner.

## 5. Engines and models

| Engine | API model | Search tool | SDK | Price verified |
|---|---|---|---|---|
| Gemini | `gemini-3.5-flash-lite` | `google_search` via `generateContent` | `google-genai==2.34.0` | 2026-11-04 |
| Claude | `claude-haiku-5-5` | `web_search_20250305` | `anthropic==2.4.0` | 2026-11-04 |
| OpenAI | `gpt-6-luna` | `web_search` via Responses API | `openai==3.45.0` | 2026-11-04 |
| Perplexity | `perplexity/sonar` via Agent API | `web_search` | `perplexityai==0.46.1` | 2026-11-04 |

- **Gemini pricing:** USD 0.30 input / USD 2.50 output per 1M tokens. Google Search is free up to 5,000 requests per month on the paid tier, then USD 14 per 1,000 requests. The project has billing enabled; only tokens are charged during the pilot.
- **Claude pricing:** Haiku 5.5 costs USD 0.10 input / USD 0.50 output per 1M tokens for prompts up to 100,000 tokens, and USD 0.50 / USD 2.50 above that threshold. Web search is USD 10 per 1,000 searches.
- **OpenAI pricing:** gpt-6-luna costs USD 0.10 input / USD 0.50 output per 1M tokens. Web search is USD 10 per 1,000 calls plus search content tokens at model rates.
- **Perplexity pricing:** perplexity/sonar costs USD 0.25 input / USD 2.50 output per 1M tokens. Web search is USD 2.50 per 1,000 invocations. The provider's `usage.cost.total_cost` is stored as `api_cost_usd` and used as the record's cost; it is not estimated.

OpenAI's free ChatGPT default moved from GPT-5.6 Luna to GPT-6 Luna in a staged rollout starting 8 October 2026; OpenAI's own help pages lagged the rollout. The pilot audits gpt-6-luna.

Perplexity does not publish its free-plan default model; third-party sources describe it as Sonar. Sonar Chat Completions ended on 27 September 2026; the pilot calls perplexity/sonar explicitly through the Agent API, without effort presets, which route to third-party models.

- **Execution:** each `(query_id, engine)` pair is queried once per run and stored as one JSONL record. The record includes the requested model, the model version reported by the API, the answer text, citations, retrieved results (when the API returns them), search count, token usage, cost, and any error.
- **Resume:** a pair already present in `runs/<run_id>/answers.jsonl` is skipped and not paid for again.
- **Ceiling:** the runner stops when cumulative known cost exceeds `PILOT_CEILING_USD` (default USD 20).
- **Nulls:** a missing cost, denominator, or usage datum is stored as `null`, never as 0.

## 6. Classification

The classifier makes one judgment per answer: whether the central fact is present and correct.

- `fact_present = yes`: the answer states the central fact correctly.
- `fact_present = no`: the answer does not state the central fact.
- `fact_present = wrong`: the answer states the central fact incorrectly.

The classifier is an LLM that only returns `yes`, `no`, or `wrong`. It is not one of the audited engines. The classifier model and the prompt version are recorded in `labels.csv`; the prompt lives in `prompts/` and every change creates a new version. The classifier is fixed in IMPL-02 before classification starts. The classifier sees only the reference fact and the answer text, never the engine name, the citations or the query. Classifier: `deepseek-flash`, prompt `prompts/classifier_v1.md`. Classifier cost is computed at the peak rate without cache discounts, as an upper bound.

Codes A–E are assigned by code, not by the classifier:

| Code | Condition |
|---|---|
| A | `fact_present=yes` and the original outlet's host appears among the citations |
| B | `fact_present=yes`, original outlet not cited, and at least one cited host is classified as Spanish-language (`es`) in `host_language.csv` |
| C | `fact_present=yes`, original outlet not cited, no Spanish-language host cited, and at least one cited host is classified as English-language (`en`) |
| D | `fact_present=yes` and no cited host is classified (no citations, or only `other` hosts) |
| E | `fact_present=no` or `fact_present=wrong` |

D is always reported split into D-none (no citations) and D-other (only unclassifiable hosts, e.g. wikipedia.org returned by Gemini without language subdomain).

If the original outlet is cited alongside others, the answer is A. If the fact is absent or wrong, the answer is E regardless of citations. Hosts are canonicalized before matching: lowercase, no `www.`, no port (`canonical_host`). If Spanish-language and English-language hosts are both cited without the original, the code is B and `en_also_cited` is recorded. A cited host absent from `host_language.csv` blocks code assignment; it is never defaulted to `other`. Hosts that publish in more than one language are marked mixed; for them, language is assigned per URL in a hand-built url_language.csv, published with the host table. A cited URL from a mixed host that is absent from url_language.csv blocks code assignment. A secondary column, original_url_cited, records whether the exact story URL is among the citations. URLs are normalized (canonical host with aliases, percent-decoded path, no trailing slash, no query string, no fragment) and compared with the story URL and any hand-listed alternate URLs of the same story (original_url_aliases). It does not change codes A–E. It is null for Gemini, whose citation URLs are redirects.

## 7. Metrics

All metrics are ratios with an explicit denominator. If the denominator is 0, the metric is `null`, not 0. Every rate is bounded by 0 and 1; the attribution gap is bounded by −1 and 1. A value outside that range is a bug.

Let `N = A + B + C + D + E` (all valid answers) and `P = A + B + C + D` (answers with the fact present and correct).

- **Fact-presence rate:** `P / N`. Denominator: all valid answers.
- **Original-attribution rate:** `A / P`. Denominator: answers with the fact present and correct.
- **Spanish-source displacement rate:** `B / P`.
- **English-source displacement rate:** `C / P`.
- **No-classified-citation rate:** `D / P`, with `D = D-none + D-other`.
- **D-none rate:** `D-none / P` (fact present, no citations).
- **D-other rate:** `D-other / P` (fact present, only unclassifiable hosts).
- **Attribution gap (Spanish minus English):**
  `A_es / P_es − A_en / P_en`.
  Denominator: answers with the fact present and correct in each language. If either language denominator is 0, the gap is `null`.
- **Breakdowns:** the same ratios with the corresponding denominators, by engine, by country and by question language. Report counts alongside every rate.

**Secondary metric — found but not cited:** the share of answers with `fact_present = yes` whose retrieved sources include the original outlet's host but whose citations do not. Denominator: answers with `fact_present = yes` from engines that expose retrieved sources distinct from citations (Gemini and Perplexity excluded; see §9). Reported per engine.

**Secondary metric — story cited:** answers with `original_url_cited = true` divided by `A + B + C + D`, for engines with non-null `original_url_cited` (Gemini excluded). Reported per engine.

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
- Perplexity's Agent API returns the sources it searched (`search_results`) but does not mark which sources support the answer when the question is sent without instructions; its documentation states that inline markers are prompt-dependent. The pilot treats Perplexity's full source list as its citations, since the source list is the backing the API exposes, analogous to the source panel in the consumer product. This likely raises Perplexity's original-attribution rate relative to engines that cite selectively, and is reported as such. Sources referenced inline (markers or URLs written in the text) are recorded as `inline_cited` and reported as a sensitivity check. The found-but-not-cited metric is not reported for Perplexity, because its citations and retrieved sources coincide. Perplexity does not document how many sources its consumer product shows, nor the API's default result budget (max_results was not set). In one manual comparison (one question, 5 November 2026), the consumer product showed 10 sources and the API returned 15; no URL appeared in both lists, and 4 of the 8 hosts shown by the consumer product appeared among the API's 10 hosts. The API and the consumer product are separate systems, and the pilot audits the API. The list is not capped.
- Citation presence is not the same as legal attribution, licensing, or payment. The pilot measures what the engine displays, not the underlying commercial arrangement.
- The classifier is an LLM; human validation covers only 20% of answers. Human coding itself may be error-prone.
- Host language is assigned manually in `host_language.csv`; hosts that publish in several languages are resolved per URL (url_language.csv), and regional or unfamiliar hosts may still be misclassified.
- **Technical limitation (Interactions API):** Google's current documentation foregrounds the Interactions API, whose citations are `url_citation` annotations. This pilot uses `generateContent` because it still exposes `grounding_metadata.grounding_chunks` and `web_search_queries` (D16). If that surface is removed, the Gemini adapter must be amended before the next real run.
- The pilot does not measure whether the original outlet was paid, only whether it was cited.
- Claude Sonnet 5.5 (successor of the documented free-plan default) was replaced by Haiku 5.5, also available on the free plan, for cost reasons, before any real query.

## 10. Amendments

None.
