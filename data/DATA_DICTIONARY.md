# Data dictionary

Schemas follow SPEC v0.2, section 6. The public versions are anonymized; the
private versions (real outlet names and URLs) live in `data/private/` and are
never committed.

## `data/stories.csv`

| Column | Type | Description |
|---|---|---|
| `story_id` | string | `S01` … `S40` |
| `outlet_id` | string | Anonymized outlet identifier (`AR-01` … `AR-05`, `CL-01` … `CL-05`) |
| `country` | enum | `AR` or `CL` |
| `url` | string | Original story URL; only in the private version |
| `english_replica_url` | string | English-language replica URL; only in the private version |
| `published_at` | string | ISO 8601 timestamp with timezone |
| `origin_criterion` | enum | `first_published`, `own_investigation`, or `attributed_exclusive` |
| `origin_evidence` | string | URL or description supporting the origin criterion |
| `fact_key` | string | The central fact in one sentence |

## `data/queries.csv`

| Column | Type | Description |
|---|---|---|
| `query_id` | string | Unique query identifier |
| `story_id` | string | Foreign key to `stories.csv` |
| `lang` | enum | `es` or `en` |
| `type` | enum | `direct` or `open` |
| `text` | string | Query text sent verbatim to the engine; never names the outlet |

## `runs/<run_id>/answers.jsonl`

One JSON object per line, one line per `(query_id, engine)` pair.

| Field | Type | Description |
|---|---|---|
| `query_id` | string | Foreign key to `queries.csv` |
| `engine` | string | `gemini`, `openai`, `claude`, or `perplexity` |
| `model_requested` | string | Model ID sent to the API |
| `model_version` | string or null | Version reported by the API |
| `run_at` | string | ISO 8601 UTC timestamp |
| `answer_text` | string or null | Answer text shown by the engine |
| `citations` | array or null | Sources the engine cites: `{url, title, host}` |
| `retrieved` | array or null | Sources searched but not necessarily cited; `null` when the engine does not expose them |
| `search_count` | integer or null | Number of searches performed |
| `search_queries` | integer or null | Perplexity only: number of subqueries in `search_results` items |
| `unresolved_markers` | integer or null | Perplexity only: inline citation markers that did not resolve to a search result |
| `api_cost_usd` | number or null | Perplexity only: provider-reported `usage.cost.total_cost` |
| `usage` | object or null | `{input_tokens, output_tokens}` |
| `cost_usd` | number or null | Estimated cost; `null` when a price or usage datum is missing |
| `error` | string or null | Error message; `null` on success |

## `data/private/labels.csv`

| Column | Type | Description |
|---|---|---|
| `query_id` | string | Foreign key to `queries.csv` |
| `engine` | string | `gemini`, `openai`, `claude`, or `perplexity` |
| `fact_present` | enum or empty | `yes`, `no`, `wrong`, or empty when the classifier returned no valid label |
| `classifier_model` | string | Model returned by the classifier API |
| `prompt_version` | string | Version of the classifier prompt in `prompts/` |
| `code` | enum or empty | `A`–`E`; assigned by code from `fact_present` and citation data; empty when `fact_present` is empty |
| `d_split` | enum or empty | `none` or `other` when `code` is `D`; empty otherwise |
| `en_also_cited` | boolean | `true` when code `B` and an English-language host is also cited; `false` otherwise |
| `human_code` | enum or empty | Human label for the blind validation sample; empty otherwise |

## `runs/<run_id>/labels_raw.jsonl`

One JSON object per line, one line per `(query_id, engine)` pair, written by
`src.classify`.

| Field | Type | Description |
|---|---|---|
| `query_id` | string | Foreign key to `queries.csv` |
| `engine` | string | `gemini`, `openai`, `claude`, or `perplexity` |
| `fact_present` | enum or null | `yes`, `no`, `wrong`; `null` when neither attempt returned a valid label |
| `raw_output` | string or null | Raw text returned by the classifier |
| `classifier_model` | string or null | Model returned by the classifier API |
| `classifier_fingerprint` | string or null | Provider `system_fingerprint` |
| `prompt_version` | string | Classifier prompt version (`classifier_v1`) |
| `usage` | object or null | `{input_tokens, output_tokens}` summed over attempts |
| `cost_usd` | number or null | Upper-bound cost at DeepSeek peak rates without cache discounts |
| `labeled_at` | string | ISO 8601 UTC timestamp |

## `data/host_language.csv`

| Column | Type | Description |
|---|---|---|
| `host` | string | Canonical host (lowercase, no `www.`, no port) |
| `language` | enum | `es`, `en`, or `other` |
| `notes` | string | Optional notes; `alias of <host>` makes the row count as `<host>` for the original-outlet match |
