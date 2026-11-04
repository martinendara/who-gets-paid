"""Claude adapter: one raw question, web_search, normalized record."""

from __future__ import annotations

import os

import anthropic

from src.hosts import canonical_host

# Docs verified 2026-11-04:
# https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
# https://platform.claude.com/docs/en/about-claude/pricing
# https://platform.claude.com/docs/en/about-claude/models/overview
MODEL_ID = "claude-sonnet-5-5"
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"  # basic search; direct calls, no dynamic filtering
MAX_OUTPUT_TOKENS = 4096
MAX_CONTINUATIONS = 3


def _error_record(message: str) -> dict:
    return {
        "engine": "claude",
        "model_requested": MODEL_ID,
        "model_version": None,
        "answer_text": None,
        "citations": None,
        "retrieved": None,
        "search_count": None,
        "usage": None,
        "error": message,
    }


def _source(source) -> dict:
    url = getattr(source, "url", None)
    title = getattr(source, "title", None)
    return {"url": url, "title": title, "host": canonical_host(url)}


def ask(query_text: str) -> dict:
    """Send ``query_text`` verbatim to Claude and return the normalized record.

    Never raises: any failure is returned in the ``error`` field with the rest
    of the record set to null.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _error_record(
            "ANTHROPIC_API_KEY is not set. Load it with: set -a && source .env && set +a"
        )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        messages = [{"role": "user", "content": query_text}]
        answer_parts: list[str] = []
        citations: list[dict] = []
        retrieved: list[dict] = []
        input_tokens = 0
        output_tokens = 0
        usage_seen = False
        search_count = 0
        model_version = None

        for _ in range(MAX_CONTINUATIONS + 1):
            response = client.messages.create(
                model=MODEL_ID,
                max_tokens=MAX_OUTPUT_TOKENS,
                messages=messages,
                tools=[{"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search"}],
            )
            model_version = getattr(response, "model", None) or model_version

            usage = getattr(response, "usage", None)
            if usage is not None:
                usage_seen = True
                input_tokens += getattr(usage, "input_tokens", 0) or 0
                output_tokens += getattr(usage, "output_tokens", 0) or 0
                server_tool_use = getattr(usage, "server_tool_use", None)
                if server_tool_use is not None:
                    search_count += (
                        getattr(server_tool_use, "web_search_requests", 0) or 0
                    )

            for block in response.content:
                block_type = getattr(block, "type", None)
                if block_type == "text":
                    text = getattr(block, "text", None)
                    if text:
                        answer_parts.append(text)
                    for citation in getattr(block, "citations", None) or []:
                        if getattr(citation, "url", None) or getattr(
                            citation, "title", None
                        ):
                            citations.append(_source(citation))
                elif block_type == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for result in content:
                            if getattr(result, "url", None) or getattr(
                                result, "title", None
                            ):
                                retrieved.append(_source(result))

            if getattr(response, "stop_reason", None) == "pause_turn":
                messages = messages + [
                    {"role": "assistant", "content": response.content}
                ]
                continue
            break
        else:
            return _error_record(
                "Claude turn still paused after maximum continuations"
            )

        return {
            "engine": "claude",
            "model_requested": MODEL_ID,
            "model_version": model_version,
            "answer_text": "".join(answer_parts),
            "citations": citations,
            "retrieved": retrieved,
            "search_count": search_count,
            "usage": {
                "input_tokens": input_tokens if usage_seen else None,
                "output_tokens": output_tokens if usage_seen else None,
            },
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - the record must always be returned
        return _error_record(f"{type(exc).__name__}: {exc}")
