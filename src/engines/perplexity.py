"""Perplexity adapter: one raw question through the Agent API, normalized record."""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import perplexity
from perplexity import Perplexity

from src.hosts import canonical_host

# Docs verified 2026-11-04:
# https://docs.perplexity.ai/docs/agent-api/quickstart
# https://docs.perplexity.ai/docs/agent-api/models
# https://docs.perplexity.ai/docs/getting-started/pricing
MODEL_ID = "perplexity/sonar"
TOOL = {"type": "web_search"}
BASE_URL = "https://api.perplexity.ai"
DEFAULT_RETRY_AFTER_SECONDS = 1.0

# Agent API citation markers, observed 2026-11-04:
#   \ue200cite\ue202turn359329search4\ue202turn359329search13\ue201
# N in turn<T>search<N> is matched against results[].id, never guessed.
_CITE_START = "\ue200"
_CITE_END = "\ue201"
_CITE_SEP = "\ue202"
_PUA_BLOCK = re.compile(_CITE_START + "(.*?)" + _CITE_END, re.S)
_TURN_MARKER = re.compile(r"turn(\d+)search(\d+)\Z")
_BRACKET_MARKER = re.compile(r"\[web:(\d+)\]|\[(\d+)\]")


def _error_record(message: str) -> dict:
    return {
        "engine": "perplexity",
        "model_requested": MODEL_ID,
        "model_version": None,
        "answer_text": None,
        "citations": None,
        "retrieved": None,
        "search_count": None,
        "usage": None,
        "error": message,
        "unresolved_markers": None,
        "search_queries": None,
        "api_cost_usd": None,
    }


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None) if response is not None else None
    return value if isinstance(value, int) else None


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _as_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _as_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _markers(text: str) -> list[tuple[str | None, int | None]]:
    """Return (turn, result id) pairs; ``None`` marks an unresolved marker."""
    blocks = _PUA_BLOCK.findall(text)
    if blocks:
        found: list[tuple[str | None, int | None]] = []
        for block in blocks:
            parts = [part for part in block.split(_CITE_SEP) if part]
            if parts and parts[0] == "cite":
                parts = parts[1:]
            for part in parts:
                match = _TURN_MARKER.fullmatch(part)
                if match:
                    found.append((match.group(1), int(match.group(2))))
                else:
                    found.append((None, None))
        return found

    # Fallback documented form, used only when no PUA block is present.
    found = []
    for match in _BRACKET_MARKER.finditer(text):
        digits = match.group(1) or match.group(2)
        found.append((None, int(digits)))
    return found


def _resolve_markers(
    markers: list[tuple[str | None, int | None]], search_items: list[dict]
) -> tuple[list[dict], int]:
    index: list[tuple[int, dict]] = []
    for item_number, item in enumerate(search_items):
        for result in item.get("results") or []:
            index.append((item_number, result))

    # Some responses may carry a turn identifier on the search_results item.
    # If present, it disambiguates repeated result ids; if absent, a repeated
    # id can never be resolved and counts as unresolved.
    item_turns: list[set[str]] = []
    for item in search_items:
        turns: set[str] = set()
        for key in ("turn", "turn_id", "id"):
            value = item.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                turns.add(str(value))
        item_turns.append(turns)

    citations: list[dict] = []
    seen_ids: set[str] = set()
    unresolved = 0
    for turn, result_id in markers:
        if result_id is None:
            unresolved += 1
            continue
        candidates = [
            (item_number, result)
            for item_number, result in index
            if str(result.get("id")) == str(result_id)
        ]
        chosen: tuple[int, dict] | None = None
        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) > 1 and turn is not None:
            narrowed = [
                candidate
                for candidate in candidates
                if turn in item_turns[candidate[0]]
            ]
            if len(narrowed) == 1:
                chosen = narrowed[0]
        if chosen is None:
            unresolved += 1
            continue

        result = chosen[1]
        result_key = str(result.get("id"))
        if result_key in seen_ids:
            continue
        seen_ids.add(result_key)
        url = result.get("url")
        citations.append(
            {
                "url": url,
                "title": result.get("title"),
                "host": canonical_host(url),
            }
        )
    return citations, unresolved


def _record_from_data(data: dict) -> dict:
    output = data.get("output") or []

    answer_parts: list[str] = []
    search_items: list[dict] = []
    retrieved: list[dict] = []
    for item in output:
        item_type = item.get("type")
        if item_type == "message":
            for content in item.get("content") or []:
                if content.get("type") != "output_text":
                    continue
                text = content.get("text")
                if text is not None:
                    answer_parts.append(text)
        elif item_type == "search_results":
            search_items.append(item)
            for result in item.get("results") or []:
                url = result.get("url")
                retrieved.append(
                    {
                        "url": url,
                        "title": result.get("title"),
                        "host": canonical_host(url),
                    }
                )

    error = data.get("error")
    if error:
        return _error_record(f"Perplexity response error: {error}")

    answer_text = "".join(answer_parts)
    status = data.get("status")
    if status is not None and status != "completed" and not answer_text:
        return _error_record(f"Perplexity response status: {status}")

    citations, unresolved = _resolve_markers(_markers(answer_text), search_items)
    search_queries = sum(len(item.get("queries") or []) for item in search_items)

    usage = data.get("usage") or {}
    tool_calls = usage.get("tool_calls_details") or {}
    search_web = tool_calls.get("search_web")
    search_count = None
    if isinstance(search_web, dict):
        search_count = _as_int(search_web.get("invocation"))
    cost = usage.get("cost") if isinstance(usage.get("cost"), dict) else {}
    api_cost_usd = _as_float(cost.get("total_cost")) if cost else None

    return {
        "engine": "perplexity",
        "model_requested": MODEL_ID,
        "model_version": data.get("model"),
        "answer_text": answer_text,
        "citations": citations,
        "retrieved": retrieved,
        "search_count": search_count,
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
        },
        "error": None,
        "unresolved_markers": unresolved,
        "search_queries": search_queries,
        "api_cost_usd": api_cost_usd,
    }


def ask(query_text: str) -> dict:
    """Send ``query_text`` verbatim to the Perplexity Agent API.

    Never raises: any failure is returned in the ``error`` field with the rest
    of the record set to null. A 429 is retried once after Retry-After.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        return _error_record(
            "PERPLEXITY_API_KEY is not set. Load it with: set -a && source .env && set +a"
        )

    try:
        # Pin the official endpoint and pass the key explicitly so the SDK does
        # not inherit PERPLEXITY_BASE_URL or other credentials from the environment.
        client = Perplexity(
            api_key=api_key,
            base_url=BASE_URL,
            max_retries=0,
        )
        for attempt in range(2):
            try:
                response = client.responses.create(
                    model=MODEL_ID,
                    input=query_text,
                    tools=[TOOL],
                )
                return _record_from_data(response.model_dump())
            except Exception as exc:  # noqa: BLE001 - the record must always be returned
                status = _status_code(exc)
                if status == 401 or isinstance(exc, perplexity.AuthenticationError):
                    print(
                        "perplexity: 401 Unauthorized. Check PERPLEXITY_API_KEY; "
                        "this record is an error.",
                        file=sys.stderr,
                    )
                    return _error_record(f"{type(exc).__name__}: {exc}")
                if (
                    attempt == 0
                    and (status == 429 or isinstance(exc, perplexity.RateLimitError))
                ):
                    delay = _retry_after_seconds(exc)
                    if delay is None:
                        delay = DEFAULT_RETRY_AFTER_SECONDS
                    time.sleep(delay)
                    continue
                return _error_record(f"{type(exc).__name__}: {exc}")
        return _error_record("Perplexity request failed after one retry")
    except Exception as exc:  # noqa: BLE001 - the record must always be returned
        return _error_record(f"{type(exc).__name__}: {exc}")
