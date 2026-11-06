# Data dictionary

Schemas follow SPEC v0.2, section 6. The public versions are anonymized; the
private versions (real outlet names and URLs) live in `data/private/` and are
never committed.

## `data/stories.csv`

| Column | Type | Description |
|---|---|---|
| `story_id` | string | `S01` … `S29` |
| `outlet_id` | string | Anonymized outlet identifier (`AR-01` … `AR-05`, `CL-01` … `CL-05`) |
| `country` | enum | `AR` or `CL` |
| `url` | string | Original story URL; only in the private version |
| `english_replica_url` | string | URL of an English-language replication that names the original outlet; empty when it was searched and none was found; `PENDING` when it has not been searched yet (D57); only in the private version |
| `published_at` | string | ISO 8601 timestamp with timezone, or a date-only `YYYY-MM-DD` when the source does not provide a time (D61); `PENDING` when not yet determined |
| `origin_criterion` | enum | `attributed_exclusive` — another outlet, in any language, credits the outlet with the story; `own_investigation` — the story rests on the outlet's own documents, records or information requests; `first_published` — the outlet labels it exclusive and no earlier publication of the fact exists |
| `origin_evidence` | string | URL or description supporting the origin criterion; stories that rest on public data are marked `public data` (D50) |
| `fact_key` | string | The central fact in one sentence; `PENDING` until it is written |
| `original_url_aliases` | string | Other URLs of the same story, separated by `|`; only in the private version; may be empty |

## `data/private/outlets.csv`

The ten outlets in the sample (five Argentina, five Chile). Private: real names and hosts are never committed.

| Column | Type | Description |
|---|---|---|
| `outlet_id` | string | Anonymized outlet identifier (`AR-01` … `AR-05`, `CL-01` … `CL-05`) |
| `name` | string | Outlet name; only in the private version |
| `country` | enum | `AR` or `CL` |
| `hosts` | string | One or more canonical hosts for the outlet, separated by `;` |
| `rank_source` | string | Source used to rank the outlet, plus any note on its replacement (D53) |

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
| `citations` | array or null | Sources the engine cites: `{url, title, host}`. For Perplexity, the full `search_results` source list (METHOD §9) |
| `retrieved` | array or null | Sources searched but not necessarily cited; `null` when the engine does not expose them |
| `search_count` | integer or null | Number of searches performed |
| `search_queries` | integer or null | Perplexity only: number of subqueries in `search_results` items |
| `unresolved_markers` | integer or null | Perplexity only: inline citation markers that did not resolve to a search result |
| `inline_cited` | array or null | Perplexity only: sources referenced inline—citation markers resolved to search results plus URLs written in the answer text—as `{url, title, host}`, deduplicated by URL. Not used for codes. `null` for other engines |
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
| `original_in_retrieved` | boolean or empty | `true` when the original outlet's host appears among retrieved sources (alias applied); `false` when retrieved is a list without it; empty when retrieved is null |
| `original_url_cited` | boolean or empty | `true` when a normalized citation URL matches the story URL or one of its `original_url_aliases` (host aliases applied); `false` otherwise; empty for Gemini or when `fact_present` is empty |
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

## `data/private/access_<run_id>.csv`

One row per story, written by `src.access_check` on the day of the run (D49, D56). The raw `robots.txt` fetched for each host is stored under `data/private/access/<run_id>/<host>_robots.txt`.

| Column | Type | Description |
|---|---|---|
| `run_id` | string | Run identifier the access check was made for |
| `checked_at` | string | ISO 8601 timestamp with timezone |
| `outlet_id` | string | Foreign key to `outlets.csv` |
| `story_id` | string | Foreign key to `stories.csv` |
| `robots_http_status` | integer or empty | Final HTTP status of `robots.txt`; empty when no response was obtained |
| `robots_status` | enum | `ok` (2xx, parsed per RFC 9309), `unavailable` (4xx, no restrictions), `unreachable` (5xx or no response, full disallow) |
| `robots_sha256` | string or empty | SHA-256 of the raw `robots.txt` body; empty when no response was obtained |
| `robots_error` | string | Network error message when `robots.txt` did not respond; empty when a response was received |
| `allow_<agent>` | boolean | Whether `robots.txt` allows the agent for the story URL, as `true`/`false`; `false` when `robots_status` is `unreachable`. Eight columns: `allow_googlebot`, `allow_google_extended`, `allow_oai_searchbot`, `allow_chatgpt_user`, `allow_claude_searchbot`, `allow_claude_user`, `allow_perplexitybot`, `allow_perplexity_user` |
| `engine_<engine>` | enum | `closed` when any of the engine's agents is disallowed for the story; `open` otherwise. Four columns: `engine_gemini`, `engine_openai`, `engine_claude`, `engine_perplexity` |
| `page_http_status` | integer or empty | HTTP status of one GET to the story URL with the declared generic user agent; empty when no response was obtained |
| `page_final_url` | string or empty | URL after redirects; empty when no response was obtained |
| `page_error` | string | Network error message when the story URL did not respond; empty when a response was received |

## `data/host_language.csv`

| Column | Type | Description |
|---|---|---|
| `host` | string | Canonical host (lowercase, no `www.`, no port) |
| `language` | enum | `es`, `en`, `other`, or `mixed` (`mixed` = language assigned per URL in `url_language.csv`) |
| `notes` | string | Optional notes; `alias of <host>` makes the row count as `<host>` for the original-outlet match |

## `data/url_language.csv`

| Column | Type | Description |
|---|---|---|
| `url` | string | Exact cited URL (no canonicalization) |
| `language` | enum | `es`, `en`, or `other`; used only for hosts marked `mixed` in `host_language.csv` |
| `notes` | string | Optional notes; the private version is `data/private/url_language_cut.csv` |
