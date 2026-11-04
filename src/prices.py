"""Price table and cost calculation for the pilot.

Prices verified 2026-11-04:
- Gemini: https://aistudio.google.com/docs/pricing
  gemini-3.5-flash-lite: USD 0.30 input / USD 2.50 output per 1M tokens.
  Google Search on the paid tier: 5,000 free requests per month (shared across
  Gemini 3 and newer), then USD 14 per 1,000 requests.
- Claude: https://platform.claude.com/docs/en/about-claude/pricing
  claude-haiku-5-5: USD 0.10 input / USD 0.50 output per 1M tokens for
  prompts up to 100,000 tokens; USD 0.50 / USD 2.50 above that threshold.
  Web search: USD 10 per 1,000 searches.
- OpenAI: https://developers.openai.com/api/docs/pricing
  gpt-6-luna: USD 0.10 input / USD 0.50 output per 1M tokens (short context).
  Web search: USD 10 per 1,000 calls plus search content tokens at model rates.
- Perplexity: https://docs.perplexity.ai/docs/agent-api/models and
  https://docs.perplexity.ai/docs/getting-started/pricing
  perplexity/sonar: USD 0.25 input / USD 2.50 output per 1M tokens; web search
  USD 2.50 per 1,000 invocations. Cost comes from the provider's
  usage.cost.total_cost (record api_cost_usd), never estimated.
"""

from __future__ import annotations

import sys

GEMINI_INPUT_USD_PER_MTOK = 0.30
GEMINI_OUTPUT_USD_PER_MTOK = 2.50
GEMINI_SEARCH_FREE_REQUESTS_PER_MONTH = 5_000
GEMINI_SEARCH_USD_PER_1000_AFTER_FREE = 14.0

# 0.0 here means "measured free under the 5,000/month allowance", not "unknown".
# The pilot's expected volume (40 stories x 4 queries) stays below that cap.
GEMINI_SEARCH_USD = 0.0
GEMINI_FREE_TIER = False  # D26: billing is enabled; tokens are billed.

CLAUDE_INPUT_USD_PER_MTOK = 0.10
CLAUDE_OUTPUT_USD_PER_MTOK = 0.50
CLAUDE_LONG_INPUT_USD_PER_MTOK = 0.50
CLAUDE_LONG_OUTPUT_USD_PER_MTOK = 2.50
CLAUDE_LONG_CONTEXT_THRESHOLD_TOKENS = 100_000
CLAUDE_SEARCH_USD_PER_1000 = 10.0

# OpenAI pricing page verified 2026-11-04.
OPENAI_INPUT_USD_PER_MTOK = 0.10
OPENAI_OUTPUT_USD_PER_MTOK = 0.50
OPENAI_SEARCH_USD_PER_1000 = 10.0


def _warn(message: str) -> None:
    print(f"prices.cost: {message}", file=sys.stderr)


def cost(record: dict) -> float | None:
    """Return the USD cost of one answer record, or ``None`` when unknowable.

    A missing price or usage datum is never treated as 0.
    """
    if record.get("error"):
        return None

    engine = record.get("engine")

    if engine == "perplexity":
        api_cost = record.get("api_cost_usd")
        if isinstance(api_cost, bool) or not isinstance(api_cost, (int, float)):
            _warn("missing api_cost_usd for engine='perplexity'; cost is null")
            return None
        return float(api_cost)

    usage = record.get("usage")
    if not isinstance(usage, dict):
        _warn(f"missing usage for engine={engine!r}; cost is null")
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, (int, float)) or not isinstance(
        output_tokens, (int, float)
    ):
        _warn(f"missing token counts for engine={engine!r}; cost is null")
        return None

    if engine == "gemini":
        token_cost = (
            input_tokens * GEMINI_INPUT_USD_PER_MTOK
            + output_tokens * GEMINI_OUTPUT_USD_PER_MTOK
        ) / 1_000_000
        # Search is 0.0 under the 5,000/month free allowance (D26).
        return token_cost + GEMINI_SEARCH_USD

    if engine == "claude":
        search_count = record.get("search_count")
        if not isinstance(search_count, (int, float)):
            _warn("missing search_count for engine='claude'; cost is null")
            return None
        if input_tokens > CLAUDE_LONG_CONTEXT_THRESHOLD_TOKENS:
            input_price = CLAUDE_LONG_INPUT_USD_PER_MTOK
            output_price = CLAUDE_LONG_OUTPUT_USD_PER_MTOK
        else:
            input_price = CLAUDE_INPUT_USD_PER_MTOK
            output_price = CLAUDE_OUTPUT_USD_PER_MTOK
        token_cost = (
            input_tokens * input_price + output_tokens * output_price
        ) / 1_000_000
        search_cost = search_count * (CLAUDE_SEARCH_USD_PER_1000 / 1_000)
        return token_cost + search_cost

    if engine == "openai":
        search_count = record.get("search_count")
        if not isinstance(search_count, (int, float)):
            _warn("missing search_count for engine='openai'; cost is null")
            return None
        token_cost = (
            input_tokens * OPENAI_INPUT_USD_PER_MTOK
            + output_tokens * OPENAI_OUTPUT_USD_PER_MTOK
        ) / 1_000_000
        search_cost = search_count * (OPENAI_SEARCH_USD_PER_1000 / 1_000)
        return token_cost + search_cost

    _warn(f"no price table for engine={engine!r}; cost is null")
    return None

# DeepSeek classifier, used as an upper bound (D32b).
# https://api-docs.deepseek.com/quick_start/pricing (verified 2026-11-04)
# deepseek-flash peak rates without cache discounts:
# USD 0.30 input (cache miss) / USD 1.20 output per 1M tokens.
DEEPSEEK_PEAK_INPUT_USD_PER_MTOK = 0.30
DEEPSEEK_PEAK_OUTPUT_USD_PER_MTOK = 1.20


def classifier_cost(usage: dict | None) -> float | None:
    """Return the upper-bound USD cost of one DeepSeek classifier call.

    Peak rate, no cache discount, always. A missing usage datum is null.
    """
    if not isinstance(usage, dict):
        _warn("missing classifier usage; cost is null")
        return None
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if not isinstance(input_tokens, (int, float)) or not isinstance(
        output_tokens, (int, float)
    ):
        _warn("missing classifier token counts; cost is null")
        return None
    return (
        input_tokens * DEEPSEEK_PEAK_INPUT_USD_PER_MTOK
        + output_tokens * DEEPSEEK_PEAK_OUTPUT_USD_PER_MTOK
    ) / 1_000_000
