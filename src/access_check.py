"""Record robots.txt and page access for every story (D49, D56).

On the day of the run, this module records whether each engine's search
agents are allowed for each story URL, per RFC 9309, and whether a generic
automated client obtains an HTTP response from the story URL. Access is an
observed variable, not an inclusion criterion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import http.client
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

USER_AGENT = (
    "who-gets-paid-access-check/0.1 "
    "(+https://github.com/martinendara/who-gets-paid)"
)
TIMEOUT_SECONDS = 20
ROBOTS_RETRIES = 1
DEFAULT_OUTLETS = Path("data/private/outlets.csv")
DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_OUT_DIR = Path("data/private")

AGENTS = (
    ("googlebot", "Googlebot", "gemini"),
    ("google_extended", "Google-Extended", "gemini"),
    ("oai_searchbot", "OAI-SearchBot", "openai"),
    ("chatgpt_user", "ChatGPT-User", "openai"),
    ("claude_searchbot", "Claude-SearchBot", "claude"),
    ("claude_user", "Claude-User", "claude"),
    ("perplexitybot", "PerplexityBot", "perplexity"),
    ("perplexity_user", "Perplexity-User", "perplexity"),
)
ENGINES = ("gemini", "openai", "claude", "perplexity")
ENGINE_COLUMNS = ("gemini", "gemini_with_extended", "openai", "claude", "perplexity")
FIELDNAMES = (
    [
        "run_id",
        "checked_at",
        "outlet_id",
        "story_id",
        "robots_http_status",
        "robots_status",
        "robots_sha256",
        "robots_error",
    ]
    + [f"allow_{slug}" for slug, _agent, _engine in AGENTS]
    + [f"engine_{engine}" for engine in ENGINE_COLUMNS]
    + ["page_http_status", "page_final_url", "page_error"]
)


class _AllowAll:
    def can_fetch(self, agent: str, url: str) -> bool:
        return True


class _BlockAll:
    def can_fetch(self, agent: str, url: str) -> bool:
        return False


def robots_status_name(http_status: int | None) -> str:
    """Map the final robots.txt status to ok, unavailable or unreachable."""
    if http_status is None:
        return "unreachable"
    if 200 <= http_status < 300:
        return "ok"
    if 400 <= http_status < 500:
        return "unavailable"
    return "unreachable"


def robots_policy(http_status: int | None, text: str):
    """Return a robots.txt policy interpreted per RFC 9309.

    2xx is parsed; any 4xx allows everything (including 401/403); 5xx and
    network failures block everything. ``RobotFileParser.read`` is not used
    because it treats 401/403 as a full disallow.
    """
    status = robots_status_name(http_status)
    if status == "unavailable":
        return _AllowAll()
    if status == "unreachable":
        return _BlockAll()
    parser = RobotFileParser()
    parser.parse(text.splitlines())
    return parser


def assess_agents(policy, url: str) -> tuple[dict[str, bool], dict[str, str]]:
    """Return per-agent allowances and per-engine open/closed for one URL."""
    allows = {
        slug: bool(policy.can_fetch(agent, url))
        for slug, agent, _engine in AGENTS
    }
    engines: dict[str, str] = {}
    engines["gemini"] = "open" if allows["googlebot"] else "closed"
    engines["gemini_with_extended"] = (
        "open"
        if allows["googlebot"] and allows["google_extended"]
        else "closed"
    )
    for engine in ("openai", "claude", "perplexity"):
        blocked = any(
            not allows[slug]
            for slug, _agent, owner in AGENTS
            if owner == engine
        )
        engines[engine] = "closed" if blocked else "open"
    return allows, engines


def fetch(
    url: str, *, retries: int = 0, timeout: float = TIMEOUT_SECONDS
):
    """GET a URL. Returns ``(status, final_url, body, error)``.

    ``status`` is None when no HTTP response was obtained after the
    configured retries. HTTP error statuses (4xx/5xx) are responses, not
    network errors.
    """
    last_error = None
    for _attempt in range(retries + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT}, method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.geturl(), response.read(), None
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
            except (OSError, http.client.HTTPException):
                body = b""
            return exc.code, exc.geturl() or url, body, None
        except (OSError, http.client.HTTPException) as exc:
            last_error = str(getattr(exc, "reason", exc)) or exc.__class__.__name__
    return None, None, None, last_error or "request failed"


def _read_csv(path: Path, required: set[str]) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty CSV or missing header")
        missing = required - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"{path}: missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        return list(reader)


def _story_url(raw: str) -> str:
    url = (raw or "").strip()
    if "://" not in url:
        url = "https://" + url
    return url


def _write_robots(dir_path: Path, host: str, body: bytes) -> str:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / f"{host}_robots.txt").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record robots.txt and page access per story (D49, D56)."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--outlets", default=str(DEFAULT_OUTLETS))
    parser.add_argument("--stories", default=str(DEFAULT_STORIES))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    args = parser.parse_args(argv)

    outlets = _read_csv(Path(args.outlets), {"outlet_id", "hosts", "country"})
    outlet_ids = {(row.get("outlet_id") or "").strip() for row in outlets}
    stories = _read_csv(Path(args.stories), {"story_id", "outlet_id", "url"})

    out_dir = Path(args.out_dir)
    robots_dir = out_dir / "access" / args.run_id
    out_path = out_dir / f"access_{args.run_id}.csv"
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")

    host_cache: dict[str, dict] = {}
    rows: list[dict] = []
    for story in stories:
        story_id = (story.get("story_id") or "").strip()
        outlet_id = (story.get("outlet_id") or "").strip()
        url = _story_url(story.get("url"))
        if not story_id or not url:
            raise SystemExit(f"{args.stories}: empty story_id or url")
        if outlet_id not in outlet_ids:
            raise SystemExit(
                f"{args.stories}: outlet_id {outlet_id!r} is not in "
                f"{args.outlets}"
            )
        host = urlparse(url).hostname
        if not host:
            raise SystemExit(f"{args.stories}: invalid URL for story {story_id!r}")

        if host not in host_cache:
            status, _final, body, error = fetch(
                f"https://{host}/robots.txt", retries=ROBOTS_RETRIES
            )
            if status is None:
                policy = robots_policy(None, "")
                sha = ""
                robots_error = error or "request failed"
                http_status: int | str = ""
            else:
                text = body.decode("utf-8", errors="replace")
                policy = robots_policy(status, text)
                sha = _write_robots(robots_dir, host, body)
                robots_error = ""
                http_status = status
            host_cache[host] = {
                "http_status": http_status,
                "status": robots_status_name(status),
                "sha": sha,
                "robots_error": robots_error,
                "policy": policy,
            }
        robots = host_cache[host]
        allows, engines = assess_agents(robots["policy"], url)

        page_status, page_final, _body, page_error = fetch(url, retries=0)
        row = {
            "run_id": args.run_id,
            "checked_at": checked_at,
            "outlet_id": outlet_id,
            "story_id": story_id,
            "robots_http_status": robots["http_status"],
            "robots_status": robots["status"],
            "robots_sha256": robots["sha"],
            "robots_error": robots["robots_error"],
            "page_http_status": (
                page_status if page_status is not None else ""
            ),
            "page_final_url": page_final if page_final is not None else "",
            "page_error": page_error or "",
        }
        for slug, _agent, _engine in AGENTS:
            row[f"allow_{slug}"] = "true" if allows[slug] else "false"
        for engine in ENGINE_COLUMNS:
            row[f"engine_{engine}"] = engines[engine]
        rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row["robots_status"]] = (
            by_status.get(row["robots_status"], 0) + 1
        )
    print(f"Stories checked: {len(rows)} ({len(host_cache)} hosts)")
    for status in ("ok", "unavailable", "unreachable"):
        if status in by_status:
            print(f"robots.txt {status}: {by_status[status]} stories")
    for engine in ENGINE_COLUMNS:
        closed = sum(1 for row in rows if row[f"engine_{engine}"] == "closed")
        print(f"{engine}: {len(rows) - closed} open / {closed} closed")
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
