"""OpenAI adapter: one raw question, web search, normalized record."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import openai

from src.hosts import canonical_host

# Docs verified 2026-11-04:
# https://developers.openai.com/api/docs/guides/tools-web-search
# https://developers.openai.com/api/docs/pricing
MODEL_ID = "gpt-6-luna"
TOOL = {"type": "web_search"}
INCLUDE = ["web_search_call.action.sources"]
BASE_URL = "https://api.openai.com/v1"
DEFAULT_RETRY_AFTER_SECONDS = 1.0


def _error_record(message: str) -> dict:
    return {
        "engine": "openai",
        "model_requested": MODEL_ID,
        "model_version": None,
        "answer_text": None,
        "citations": None,
        "retrieved": None,
        "search_count": None,
        "usage": None,
        "error": message,
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


def _record(response) -> dict:
    data = response.model_dump()
    output = data.get("output") or []

    answer_parts: list[str] = []
    citations: list[dict] = []
    retrieved: list[dict] = []
    sources_seen = False
    search_count = 0

    for item in output:
        item_type = item.get("type")
        if item_type == "message":
            for content in item.get("content") or []:
                if content.get("type") != "output_text":
                    continue
                text = content.get("text")
                if text:
                    answer_parts.append(text)
                for annotation in content.get("annotations") or []:
                    if annotation.get("type") != "url_citation":
                        continue
                    url = annotation.get("url")
                    citations.append(
                        {
                            "url": url,
                            "title": annotation.get("title"),
                            "host": canonical_host(url),
                        }
                    )
        elif item_type == "web_search_call":
            action = item.get("action") or {}
            if action.get("type") == "search":
                search_count += 1
            sources = action.get("sources")
            if sources is not None:
                sources_seen = True
                for source in sources:
                    url = source.get("url")
                    retrieved.append(
                        {
                            "url": url,
                            "title": source.get("title"),
                            "host": canonical_host(url),
                        }
                    )

    error = data.get("error")
    if error:
        return _error_record(f"OpenAI response error: {error}")

    answer_text = getattr(response, "output_text", None)
    if answer_text is None:
        answer_text = "".join(answer_parts)

    status = data.get("status")
    if status is not None and status != "completed" and not answer_text:
        return _error_record(f"OpenAI response status: {status}")

    usage = data.get("usage") or {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    # output_tokens already includes output_tokens_details.reasoning_tokens
    # (verified against total_tokens on 2026-11-04); do not add them again.

    return {
        "engine": "openai",
        "model_requested": MODEL_ID,
        "model_version": data.get("model"),
        "answer_text": answer_text,
        "citations": citations,
        "retrieved": retrieved if sources_seen else None,
        "search_count": search_count,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "error": None,
    }


def ask(query_text: str) -> dict:
    """Send ``query_text`` verbatim to OpenAI and return the normalized record.

    Never raises: any failure is returned in the ``error`` field with the rest
    of the record set to null. A 429 is retried once after Retry-After.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _error_record(
            "OPENAI_API_KEY is not set. Load it with: set -a && source .env && set +a"
        )

    try:
        # Pin the official endpoint and pass the key explicitly so the SDK does
        # not inherit OPENAI_BASE_URL or other credentials from the environment.
        client = openai.OpenAI(
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
                    include=INCLUDE,
                )
                return _record(response)
            except Exception as exc:  # noqa: BLE001 - the record must always be returned
                status = _status_code(exc)
                if status == 401 or isinstance(exc, openai.AuthenticationError):
                    print(
                        "openai: 401 Unauthorized. Check OPENAI_API_KEY; "
                        "this record is an error.",
                        file=sys.stderr,
                    )
                    return _error_record(f"{type(exc).__name__}: {exc}")
                if (
                    attempt == 0
                    and (status == 429 or isinstance(exc, openai.RateLimitError))
                ):
                    delay = _retry_after_seconds(exc)
                    if delay is None:
                        delay = DEFAULT_RETRY_AFTER_SECONDS
                    time.sleep(delay)
                    continue
                return _error_record(f"{type(exc).__name__}: {exc}")
        return _error_record("OpenAI request failed after one retry")
    except Exception as exc:  # noqa: BLE001 - the record must always be returned
        return _error_record(f"{type(exc).__name__}: {exc}")
