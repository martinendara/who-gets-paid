"""Price table and cost calculation for the pilot.

Prices verified 2026-11-04:
- Gemini: https://aistudio.google.com/docs/pricing
  gemini-3.5-flash-lite: USD 0.30 input / USD 2.50 output per 1M tokens.
  Google Search on the paid tier: 5,000 free requests per month (shared across
  Gemini 3 and newer), then USD 14 per 1,000 requests.
- Claude: https://platform.claude.com/docs/en/about-claude/pricing
  claude-sonnet-5-5: USD 2.00 input / USD 10.00 output per 1M tokens.
  Web search: USD 10 per 1,000 searches.
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

CLAUDE_INPUT_USD_PER_MTOK = 2.00
CLAUDE_OUTPUT_USD_PER_MTOK = 10.00
CLAUDE_SEARCH_USD_PER_1000 = 10.0


def _warn(message: str) -> None:
    print(f"prices.cost: {message}", file=sys.stderr)


def cost(record: dict) -> float | None:
    """Return the USD cost of one answer record, or ``None`` when unknowable.

    A missing price or usage datum is never treated as 0.
    """
    if record.get("error"):
        return None

    engine = record.get("engine")
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
        token_cost = (
            input_tokens * CLAUDE_INPUT_USD_PER_MTOK
            + output_tokens * CLAUDE_OUTPUT_USD_PER_MTOK
        ) / 1_000_000
        search_cost = search_count * (CLAUDE_SEARCH_USD_PER_1000 / 1_000)
        return token_cost + search_cost

    _warn(f"no price table for engine={engine!r}; cost is null")
    return None
