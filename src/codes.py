"""Assign A-E codes from blind labels, citations and host language."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from src.hosts import canonical_host, normalize_url

DEFAULT_QUERIES = Path("data/private/queries.csv")
DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_HOST_LANGUAGE = Path("data/host_language.csv")
DEFAULT_URL_LANGUAGE = Path("data/private/url_language.csv")
DEFAULT_OUT = Path("data/private/labels.csv")
DEFAULT_RUNS_DIR = Path("runs")
HOST_LANGUAGES = {"es", "en", "other", "mixed"}
URL_LANGUAGES = {"es", "en", "other"}
FACT_LABELS = {"yes", "no", "wrong"}
ALIAS_PREFIX = "alias of "
FIELDNAMES = [
    "query_id",
    "engine",
    "fact_present",
    "classifier_model",
    "prompt_version",
    "code",
    "d_split",
    "en_also_cited",
    "original_in_retrieved",
    "original_url_cited",
    "human_code",
]


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: malformed JSON: {exc}") from exc


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


def _latest_answers(path: Path) -> dict:
    latest: dict = {}
    for record in _read_jsonl(path):
        pair = (record.get("query_id"), record.get("engine"))
        if not record.get("error"):
            latest[pair] = record
    return latest


def _latest_labels(path: Path) -> dict:
    latest: dict = {}
    for record in _read_jsonl(path):
        pair = (record.get("query_id"), record.get("engine"))
        latest[pair] = record
    return latest


def _read_host_language(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    languages: dict[str, str] = {}
    aliases: dict[str, str] = {}
    for row in _read_csv(path, {"host", "language"}):
        host = canonical_host(row.get("host"))
        if not host:
            raise SystemExit(f"{path}: invalid host {row.get('host')!r}")
        language = (row.get("language") or "").strip().lower()
        if language not in HOST_LANGUAGES:
            raise SystemExit(
                f"{path}: host {host!r} has unsupported language {language!r}"
            )
        if host in languages and languages[host] != language:
            raise SystemExit(f"{path}: conflicting languages for host {host!r}")
        languages[host] = language

        note = (row.get("notes") or row.get("note") or "").strip()
        if note.lower().startswith(ALIAS_PREFIX):
            target = canonical_host(note[len(ALIAS_PREFIX):].strip())
            if not target:
                raise SystemExit(f"{path}: invalid alias note {note!r}")
            if host in aliases and aliases[host] != target:
                raise SystemExit(f"{path}: conflicting aliases for host {host!r}")
            aliases[host] = target
    return languages, aliases


def _read_url_language(path: Path) -> dict[str, str]:
    """Read exact-URL languages for hosts marked ``mixed``.

    A missing file is an empty map; a cited URL that is absent then blocks
    code assignment, as does a host missing from ``host_language.csv``.
    """
    languages: dict[str, str] = {}
    if not path.exists():
        return languages
    for row in _read_csv(path, {"url", "language"}):
        url = (row.get("url") or "").strip()
        if not url:
            raise SystemExit(f"{path}: empty url")
        language = (row.get("language") or "").strip().lower()
        if language not in URL_LANGUAGES:
            raise SystemExit(
                f"{path}: url {url!r} has unsupported language {language!r}"
            )
        if url in languages and languages[url] != language:
            raise SystemExit(f"{path}: conflicting languages for url {url!r}")
        languages[url] = language
    return languages


def _assign(
    fact_present: str | None,
    citations: list[tuple[str, str | None]],
    original_host: str,
    host_languages: dict[str, str],
    url_languages: dict[str, str],
    aliases: dict[str, str],
) -> tuple[str, str, bool]:
    if fact_present is None:
        return "", "", False
    if fact_present in ("no", "wrong"):
        return "E", "", False

    effective = [aliases.get(host, host) for host, _ in citations]
    if original_host in effective:
        return "A", "", False

    languages: list[str] = []
    for host, url in citations:
        language = host_languages[host]
        if language == "mixed":
            language = url_languages.get(url)
            if language is None:
                raise SystemExit(
                    f"no url_language entry for {url!r} (host {host!r})"
                )
        languages.append(language)
    has_es = "es" in languages
    has_en = "en" in languages
    if has_es:
        return "B", "", has_en
    if has_en:
        return "C", "", False
    return "D", ("none" if not citations else "other"), False


def _original_in_retrieved(
    original_host: str,
    retrieved,
    aliases: dict[str, str],
) -> bool | None:
    """Return whether the original host appears in retrieved sources.

    ``None`` means retrieved is null or not a list (Gemini does not expose it).
    """
    if not isinstance(retrieved, list):
        return None
    effective_original = aliases.get(original_host, original_host)
    for item in retrieved:
        if not isinstance(item, dict):
            continue
        host = item.get("host")
        if not host:
            host = canonical_host(item.get("url"))
        if host is None:
            continue
        if host == original_host or aliases.get(host, host) == effective_original:
            return True
    return False


def _normalize_for_match(url: str | None, aliases: dict[str, str]) -> str | None:
    normalized = normalize_url(url)
    if normalized is None:
        return None
    host, slash, path = normalized.partition("/")
    return aliases.get(host, host) + slash + path


def _original_url_cited(
    engine: str | None,
    fact_present: str | None,
    citations: list[tuple[str, str | None]],
    story_urls: list[str],
    aliases: dict[str, str],
) -> bool | None:
    """Return whether a citation matches the story URL or an alternate URL.

    ``None`` for Gemini (citation URLs are Vertex redirects) and when the
    classifier returned no label.
    """
    if engine == "gemini" or fact_present is None:
        return None
    targets = {
        normalized
        for normalized in (
            _normalize_for_match(story_url, aliases) for story_url in story_urls
        )
        if normalized is not None
    }
    for _, url in citations:
        normalized = _normalize_for_match(url, aliases)
        if normalized is not None and normalized in targets:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assign A-E codes from blind labels and citations."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument(
        "--queries", default=str(DEFAULT_QUERIES), help="queries.csv path"
    )
    parser.add_argument(
        "--stories", default=str(DEFAULT_STORIES), help="stories.csv path"
    )
    parser.add_argument(
        "--host-language",
        default=str(DEFAULT_HOST_LANGUAGE),
        help="host_language.csv path",
    )
    parser.add_argument(
        "--url-language",
        default=str(DEFAULT_URL_LANGUAGE),
        help="url_language.csv path (exact URLs for mixed hosts)",
    )
    parser.add_argument(
        "--out", default=str(DEFAULT_OUT), help="labels.csv output path"
    )
    args = parser.parse_args(argv)

    run_dir = DEFAULT_RUNS_DIR / args.run_id
    answers_path = run_dir / "answers.jsonl"
    labels_path = run_dir / "labels_raw.jsonl"
    if not answers_path.exists():
        raise SystemExit(f"answers file not found: {answers_path}")
    if not labels_path.exists():
        raise SystemExit(f"labels file not found: {labels_path}")

    answers = _latest_answers(answers_path)
    labels = _latest_labels(labels_path)

    query_to_story: dict[str, str] = {}
    for row in _read_csv(Path(args.queries), {"query_id", "story_id"}):
        query_id = (row.get("query_id") or "").strip()
        story_id = (row.get("story_id") or "").strip()
        if not query_id or not story_id:
            raise SystemExit(f"{args.queries}: empty query_id or story_id")
        if query_id in query_to_story:
            raise SystemExit(f"{args.queries}: duplicate query_id {query_id!r}")
        query_to_story[query_id] = story_id

    story_to_urls: dict[str, list[str]] = {}
    for row in _read_csv(Path(args.stories), {"story_id", "url"}):
        story_id = (row.get("story_id") or "").strip()
        url = (row.get("url") or "").strip()
        if not story_id or not url:
            raise SystemExit(f"{args.stories}: empty story_id or url")
        if story_id in story_to_urls:
            raise SystemExit(f"{args.stories}: duplicate story_id {story_id!r}")
        alias_text = (row.get("original_url_aliases") or "").strip()
        story_to_urls[story_id] = [url] + [
            part.strip() for part in alias_text.split("|") if part.strip()
        ]

    host_languages, aliases = _read_host_language(Path(args.host_language))
    url_languages = _read_url_language(Path(args.url_language))

    pending: list[
        tuple[
            dict,
            str | None,
            list[tuple[str, str | None]],
            str,
            object,
            bool | None,
        ]
    ] = []
    missing_hosts: set[str] = set()
    missing_urls: set[str] = set()
    for pair, label in labels.items():
        query_id, engine = pair
        if query_id not in query_to_story:
            raise SystemExit(
                f"query_id {query_id!r} is not in {args.queries}"
            )
        story_id = query_to_story[query_id]
        if story_id not in story_to_urls:
            raise SystemExit(
                f"story_id {story_id!r} is not in {args.stories}"
            )
        original_host = canonical_host(story_to_urls[story_id][0])
        if not original_host:
            raise SystemExit(
                f"{args.stories}: invalid URL for story {story_id!r}"
            )

        answer = answers.get(pair)
        if answer is None:
            raise SystemExit(
                f"no error-free answer for {query_id!r}/{engine!r}"
            )

        fact_present = label.get("fact_present")
        if fact_present not in FACT_LABELS and fact_present is not None:
            raise SystemExit(
                f"invalid fact_present {fact_present!r} for "
                f"{query_id!r}/{engine!r}"
            )

        citations: list[tuple[str, str | None]] = []
        for citation in answer.get("citations") or []:
            url = citation.get("url")
            host = citation.get("host")
            if not host:
                host = canonical_host(url)
            if host is None:
                missing_hosts.add(f"<no-host: {url}>")
                continue
            citations.append((host, url))
            if host not in host_languages:
                missing_hosts.add(host)
                continue
            if host_languages[host] == "mixed" and (
                url is None or url not in url_languages
            ):
                missing_urls.add(url if url is not None else f"<no-url: {host}>")
        original_url_cited = _original_url_cited(
            engine,
            fact_present,
            citations,
            story_to_urls[story_id],
            aliases,
        )
        pending.append(
            (
                label,
                fact_present,
                citations,
                original_host,
                answer.get("retrieved"),
                original_url_cited,
            )
        )

    if missing_hosts or missing_urls:
        if missing_hosts:
            print(
                f"Hosts absent from {args.host_language}; "
                f"no codes were assigned:",
                file=sys.stderr,
            )
            for host in sorted(missing_hosts):
                print(f"  {host}", file=sys.stderr)
        if missing_urls:
            print(
                f"URLs absent from {args.url_language} for mixed hosts; "
                f"no codes were assigned:",
                file=sys.stderr,
            )
            for url in sorted(missing_urls):
                print(f"  {url}", file=sys.stderr)
        return 4

    rows: list[dict] = []
    for (
        label,
        fact_present,
        citations,
        original_host,
        retrieved,
        original_url_cited,
    ) in pending:
        code, d_split, en_also_cited = _assign(
            fact_present,
            citations,
            original_host,
            host_languages,
            url_languages,
            aliases,
        )
        original_in_retrieved = _original_in_retrieved(
            original_host, retrieved, aliases
        )
        rows.append(
            {
                "query_id": label.get("query_id") or "",
                "engine": label.get("engine") or "",
                "fact_present": fact_present or "",
                "classifier_model": label.get("classifier_model") or "",
                "prompt_version": label.get("prompt_version") or "",
                "code": code,
                "d_split": d_split,
                "en_also_cited": "true" if en_also_cited else "false",
                "original_in_retrieved": (
                    ""
                    if original_in_retrieved is None
                    else ("true" if original_in_retrieved else "false")
                ),
                "original_url_cited": (
                    ""
                    if original_url_cited is None
                    else ("true" if original_url_cited else "false")
                ),
                "human_code": "",
            }
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {out_path}")
    counts = Counter(row["code"] or "(none)" for row in rows)
    print(
        "Codes: "
        + ", ".join(f"{code}={counts[code]}" for code in sorted(counts))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
