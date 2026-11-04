"""Gemini adapter: one raw question, Google Search grounding, normalized record."""

from __future__ import annotations

import os

from google import genai
from google.genai import types

from src.hosts import canonical_host

# Docs verified 2026-11-04:
# https://aistudio.google.com/docs/models
# https://aistudio.google.com/docs/google-search
# https://aistudio.google.com/docs/pricing
MODEL_ID = "gemini-3.5-flash-lite"
SEARCH_TOOL = "google_search"  # no version suffix; sent via generateContent (D16)
MAX_OUTPUT_TOKENS = 4096


def _error_record(message: str) -> dict:
    return {
        "engine": "gemini",
        "model_requested": MODEL_ID,
        "model_version": None,
        "answer_text": None,
        "citations": None,
        "retrieved": None,
        "search_count": None,
        "usage": None,
        "error": message,
    }


def ask(query_text: str) -> dict:
    """Send ``query_text`` verbatim to Gemini and return the normalized record.

    Never raises: any failure is returned in the ``error`` field with the rest
    of the record set to null.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return _error_record(
            "GEMINI_API_KEY is not set. Load it with: set -a && source .env && set +a"
        )

    try:
        client = genai.Client(api_key=api_key)
        config = types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=query_text,
            config=config,
        )

        candidates = response.candidates or []
        if not candidates:
            return _error_record("Gemini returned no candidates")

        candidate = candidates[0]

        answer_parts: list[str] = []
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = getattr(part, "text", None)
            if text and not getattr(part, "thought", False):
                answer_parts.append(text)

        citations: list[dict] = []
        search_count = 0
        grounding = getattr(candidate, "grounding_metadata", None)
        if grounding is not None:
            for chunk in getattr(grounding, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web is None:
                    continue
                # D16: the host comes from web.title; web.uri is a Vertex redirect.
                title = getattr(web, "title", None)
                citations.append(
                    {
                        "url": getattr(web, "uri", None),
                        "title": title,
                        "host": canonical_host(title),
                    }
                )
            search_count = len(getattr(grounding, "web_search_queries", None) or [])

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = None
        if usage is not None:
            candidates_tokens = getattr(usage, "candidates_token_count", None)
            if candidates_tokens is None:
                candidates_tokens = getattr(usage, "response_token_count", None)
            if candidates_tokens is not None:
                # Thinking tokens are billed as output too.
                output_tokens = candidates_tokens + (
                    getattr(usage, "thoughts_token_count", None) or 0
                )

        return {
            "engine": "gemini",
            "model_requested": MODEL_ID,
            "model_version": getattr(response, "model_version", None),
            "answer_text": "".join(answer_parts),
            "citations": citations,
            "retrieved": None,
            "search_count": search_count,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - the record must always be returned
        return _error_record(f"{type(exc).__name__}: {exc}")
