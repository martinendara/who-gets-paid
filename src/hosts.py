"""Canonicalize a URL or a host-like title to a bare host."""

from __future__ import annotations

from urllib.parse import urlparse


def canonical_host(s: str | None) -> str | None:
    """Return a lowercase host without ``www.`` or a port, or ``None``.

    Accepts a full URL, a protocol-relative URL, a bare host, or a title whose
    first whitespace-delimited token looks like a host (Gemini returns the
    source host in ``grounding_chunk.web.title``). Returns ``None`` when no
    host can be extracted.
    """
    if not s:
        return None

    raw = s.strip()
    if not raw:
        return None

    host: str | None
    try:
        if "://" in raw:
            host = urlparse(raw).hostname
        elif raw.startswith("//"):
            host = urlparse("http:" + raw).hostname
        else:
            token = raw.split()[0].strip(".,;:()[]<>\"'")
            token = token.split("/", 1)[0]
            if "@" in token:
                token = token.rsplit("@", 1)[1]
            if token.count(":") == 1:
                token = token.split(":", 1)[0]
            host = token
    except ValueError:
        return None

    if not host:
        return None

    host = host.strip().strip(".").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host:
        return None
    return host
