# Method

**Preregistered on 2026-11-04. Any change after the first real query is logged under Amendments with date.**

## 1. Research question

When an AI engine answers a question in Spanish about a fact that a Spanish-language outlet published first, does the engine cite the original outlet? And how does that compare with the same fact asked in English?

## 2. Hypothesis

In Spanish-language answers, the original outlet is cited less often than in English-language answers to the same facts; part of the use is displaced to English-language sources.

The hypothesis is directional but does not fix a magnitude. The pilot reports descriptive rates and their denominators; it is not powered for a causal claim.

## 3. Sampling frame

- **Outlets:** in Argentina, the four most-used Spanish-language written or digital-first national news brands plus one leading regional daily; in Chile, the five most-used Spanish-language written or digital-first news brands (broadcast-led brands excluded). They are listed in `data/private/outlets.csv` with the source used to rank them.
- **Stories:** each story is a scoop that originated in the outlet in its home country. Origin is established by at least one of: another outlet, in any language, credits the outlet with the story; the story rests on the outlet's own investigation (documents, records, information requests); or the outlet labels it exclusive and no earlier publication exists. Whether an English-language outlet later replicated the story, naming the original outlet, is recorded (`english_replica_url`, private; empty when none was found) and is an observed variable, not an inclusion criterion; for each story at least one English-language web search for the fact and the outlet name was run on 6 November 2026, so an empty value means none was found by that search, not that none exists. Displacement to English-language sources (C) is therefore measured unconditionally, and results are also reported split by stories with and without an English replica. At most two stories per outlet come from the same ongoing news saga. Each story's central fact is stated in `fact_key`.
- **Size:** 10 outlets (5 Argentina, 5 Chile), 29 stories, 116 queries (4 per story), 464 answers (4 engines). Each outlet contributes at least 3 stories, except two outlets with 2 stories each, for which no further story passed the origin test; their results are reported with that caveat.
- **Publication window:** stories published between 2025-11-16 and 2026-08-16 inclusive. The window was fixed when the sample was closed, before any sample query.
- **Origin test:** a story qualifies only if (1) no one had published the same fact or the same angle before, and (2) no story from the same day carries the same angle without crediting the outlet. That the underlying data is public does not disqualify a story; it is marked "public data" in `origin_evidence`.
- **Sampling note:** one outlet from the initial list was dropped because fewer than three stories passed the origin test within the publication window. This is a limit of the sampling, not a finding about that outlet. It was replaced by the next outlet in the same ranking (Google visibility 2025), not counting the outlet dropped or the one already excluded by D47.
- **Origin criteria** (`origin_criterion`, at least one per story, recorded with evidence in `origin_evidence`): `attributed_exclusive` — another outlet, in any language, credits the outlet with the story; `own_investigation` — the story rests on the outlet's own documents, records or information requests; `first_published` — the outlet labels it exclusive and no earlier publication of the fact exists.
- **Origin evidence:** each story records a URL or a description supporting the criterion in `origin_evidence` (private version keeps the URL).
- **Selection:** the sample is purposive, not probabilistic. It is fixed in `stories.csv` before the first real query. No story is added or removed after seeing model outputs.
- **Private/public split:** `data/private/` holds URLs and real outlet names and is never committed. The public `data/stories.csv` is anonymized.
- **Feasibility run (D33):** before the first query of the sample, a technical run with 5 stories that are NOT part of the 29-story sample checks that all four engines and the parser work. Its `run_id` starts with `cut-`, and it is excluded from every metric.

### Access to outlet sites (observed variable)

On the day of the run, for each outlet we fetch robots.txt and record, for the URL of each story, whether it allows each engine's search agents: Gemini (Googlebot, Google-Extended), OpenAI (OAI-SearchBot, ChatGPT-User), Claude (Claude-SearchBot, Claude-User), Perplexity (PerplexityBot, Perplexity-User). An engine is 'closed' for a story if any of its agents is disallowed, except as noted for Gemini.

For Gemini, open/closed is determined by Googlebot only; Google-Extended is recorded and a sensitivity variant (closed if Googlebot or Google-Extended is disallowed) is reported, because Google's documentation, as of 6 November 2026, describes Google-Extended as governing grounding in Gemini Apps and in Grounding with Google Search on Vertex AI, while the pilot uses the Gemini Developer API.

robots.txt is interpreted per RFC 9309: 2xx parsed; 4xx means no restrictions; 5xx or unreachable means full disallow. We also fetch each story URL once with a declared generic user agent and record the HTTP status. We do not impersonate engine crawlers, so this measures whether a site rejects a generic automated client, not engine-specific blocking. Code A is also reported split by open/closed per engine. Access is not an inclusion criterion.

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

Amended on 2026-11-09; see §10.

- A random 20% sample of labeled `(query_id, engine)` pairs is validated by a human coder.
- Sampling is stratified by engine and, where possible, by answer language. The sampling frame is the set of answers with a valid record.
- The human coder is blind to the classifier's label and to the engine name. The coder sees the query, the answer text, the citations, and the host-language table, and assigns `fact_present` (yes/no/wrong) and the A–E code.
- Agreement is the proportion of exact matches between the classifier and the human coder on `fact_present`.
- Cohen's kappa is computed for the three-category `fact_present` label, unweighted. If the sample contains only one observed category, kappa is `null` rather than 0.
- Report the number of validated answers, agreement, and kappa. The validation sample is stored in `labels.csv` (`human_code`; empty for records outside the sample).

## 9. Limitations

- The sample is purposive and small (10 outlets, 29 stories, 4 queries per story); results are descriptive and not generalizable to all Spanish-language media.
- Engines are proprietary and change over time. Each record stores `run_at` and `model_version`; a later run is a new observation, not a replication of the same engine state.
- Search results vary with location, time, and personalization. The runner does not control search location beyond API defaults.
- Perplexity's Agent API returns the sources it searched (`search_results`) but does not mark which sources support the answer when the question is sent without instructions; its documentation states that inline markers are prompt-dependent. The pilot treats Perplexity's full source list as its citations, since the source list is the backing the API exposes, analogous to the source panel in the consumer product. This likely raises Perplexity's original-attribution rate relative to engines that cite selectively, and is reported as such. Sources referenced inline (markers or URLs written in the text) are recorded as `inline_cited` and reported as a sensitivity check. The found-but-not-cited metric is not reported for Perplexity, because its citations and retrieved sources coincide. Perplexity does not document how many sources its consumer product shows, nor the API's default result budget (max_results was not set). In one manual comparison (one question, 5 November 2026), the consumer product showed 10 sources and the API returned 15; no URL appeared in both lists, and 4 of the 8 hosts shown by the consumer product appeared among the API's 10 hosts. The API and the consumer product are separate systems, and the pilot audits the API. The list is not capped.
- Citation presence is not the same as legal attribution, licensing, or payment. The pilot measures what the engine displays, not the underlying commercial arrangement.
- The classifier is an LLM; human validation covers only 20% of answers. Human coding itself may be error-prone.
- Host language is assigned manually in `host_language.csv`; hosts that publish in several languages are resolved per URL (url_language.csv), and regional or unfamiliar hosts may still be misclassified.
- **Technical limitation (Interactions API):** Google's current documentation foregrounds the Interactions API, whose citations are `url_citation` annotations. This pilot uses `generateContent` because it still exposes `grounding_metadata.grounding_chunks` and `web_search_queries` (D16). If that surface is removed, the Gemini adapter must be amended before the next real run.
- The pilot does not measure whether the original outlet was paid, only whether it was cited.
- Claude Sonnet 5.5 (successor of the documented free-plan default) was replaced by Haiku 5.5, also available on the free plan, for cost reasons, before any real query.
- A random 25% of the queries (29 of 116, seed 20261106, selected by `src/sample_repeat.py`) is re-run on all four engines seven days after the first day of the main run; both dates are recorded with the results. Agreement of codes between the two runs is reported per engine. The main metrics use only the main run.
- Code A means that the engine cited the original outlet's host; the pilot does not verify that the cited page supports the fact stated in the answer. Checking citation support is left for future work.

## 10. Amendments

### 2026-11-09 — Validation design (amends §8; recorded before any validation result was seen)
The 20% random human sample in §8 is replaced by targeted adjudication. (1) A second blind classifier, from a model family that is neither audited nor the first classifier's (NVIDIA Nemotron via OpenRouter; the exact model id returned by the API is stored with every label), labels every answer with the same prompt (`classifier_v1`) and the same inputs (`fact_key` and answer text only). (2) Where the two classifiers disagree on `fact_present`, a human coder decides. (3) The human coder also labels 30 randomly selected pairs on which the classifiers agree (seed 20261109), to estimate shared errors. (4) The human coder sees the `fact_key`, the query and the answer text, and is blind to the engine and to both classifiers' labels. The human assigns `fact_present` only (yes/no/wrong); codes A–E follow from `fact_present` by the rules in §6. (5) The final `fact_present` is the human label for every pair the human coded, and the agreed label otherwise. Reported: agreement and Cohen's kappa between the two classifiers; agreement of each classifier with the human on the pairs the human coded; and the number of errors the human found among the 30 agreement checks. Reason: concentrate human effort where automated labels are uncertain while keeping a human check of shared errors. The provider's data-retention policy for this free endpoint is not stated in OpenRouter's API metadata (checked 2026-11-09); only the fact_key and the engine's answer text are sent.

### 2026-11-09 — Uncertainty intervals
In addition to counts, every rate in §7 is reported with a 95% percentile interval from a bootstrap that resamples stories with replacement (2,000 resamples, seed 20261109), because answers are clustered within 29 stories.

### 2026-11-11 — Human coding guideline and rounding check (recorded after human coding)
The human coder compared each answer with the `fact_key`, not with external reality. An answer counts as stating the fact when it conveys its central claim, even with different wording, emphasis or rounded figures; `wrong` is meant only for a different key detail (figure, person, date or place). Because rounded figures may have been coded as `wrong`, every human `wrong` label was then re-checked by the first classifier with a single question (`prompts/rounding_check_v1.md`): whether the only difference is a rounded or approximated figure. Where it answered yes, the final label is `yes`; otherwise the human label stands. The original human labels and the check outputs are kept (`runs/<run_id>/rounding_check.jsonl`, `review_<run_id>.before_rounding.csv`, private), and the number of labels changed is reported.
