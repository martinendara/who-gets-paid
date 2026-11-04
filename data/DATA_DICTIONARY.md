# Data dictionary

Schemas follow SPEC v0.2, section 6. The public versions are anonymized; the
private versions (real outlet names and URLs) live in `data/private/` and are
never committed.

## `data/stories.csv`

| Column | Type | Description |
|---|---|---|
| `story_id` | string | `S01` … `S40` |
| `outlet_id` | string | Anonymized outlet identifier (`AR-L1`, `AR-S3`, `CL-S1`, …) |
| `outlet_size` | enum | `large` or `small` |
| `country` | enum | `AR` or `CL` |
| `url` | string | Original story URL; only in the private version |
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
| `engine` | string | `gemini` or `claude` |
| `model_requested` | string | Model ID sent to the API |
| `model_version` | string or null | Version reported by the API |
| `run_at` | string | ISO 8601 UTC timestamp |
| `answer_text` | string or null | Answer text shown by the engine |
| `citations` | array or null | Sources the engine cites: `{url, title, host}` |
| `retrieved` | array or null | Sources searched but not necessarily cited; `null` when the engine does not expose them |
| `search_count` | integer or null | Number of searches performed |
| `usage` | object or null | `{input_tokens, output_tokens}` |
| `cost_usd` | number or null | Estimated cost; `null` when a price or usage datum is missing |
| `error` | string or null | Error message; `null` on success |

## `data/labels.csv`

| Column | Type | Description |
|---|---|---|
| `query_id` | string | Foreign key to `queries.csv` |
| `engine` | string | `gemini` or `claude` |
| `fact_present` | enum | `yes`, `no`, or `wrong` |
| `classifier_model` | string | Model used for the yes/no/wrong judgment |
| `prompt_version` | string | Version of the classifier prompt in `prompts/` |
| `code` | enum | `A`–`E`; assigned by code from `fact_present` and citation data |
| `human_code` | enum or empty | Human label for the blind validation sample; empty otherwise |

## `data/host_language.csv`

| Column | Type | Description |
|---|---|---|
| `host` | string | Canonical host (lowercase, no `www.`, no port) |
| `language` | enum | `es`, `en`, or `other` |
| `notes` | string | Optional notes on the classification |
