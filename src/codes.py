"""Assign A-E codes from blind labels, citations and host language."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

from src.hosts import canonical_host

DEFAULT_QUERIES = Path("data/private/queries.csv")
DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_HOST_LANGUAGE = Path("data/host_language.csv")
DEFAULT_OUT = Path("data/private/labels.csv")
DEFAULT_RUNS_DIR = Path("runs")
LANGUAGES = {"es", "en", "other"}
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
        if language not in LANGUAGES:
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


def _assign(
    fact_present: str | None,
    hosts: list[str],
    original_host: str,
    host_languages: dict[str, str],
    aliases: dict[str, str],
) -> tuple[str, str, bool]:
    if fact_present is None:
        return "", "", False
    if fact_present in ("no", "wrong"):
        return "E", "", False

    effective = [aliases.get(host, host) for host in hosts]
    if original_host in effective:
        return "A", "", False

    languages = [host_languages[host] for host in hosts]
    has_es = "es" in languages
    has_en = "en" in languages
    if has_es:
        return "B", "", has_en
    if has_en:
        return "C", "", False
    return "D", ("none" if not hosts else "other"), False


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

    story_to_url: dict[str, str] = {}
    for row in _read_csv(Path(args.stories), {"story_id", "url"}):
        story_id = (row.get("story_id") or "").strip()
        url = (row.get("url") or "").strip()
        if not story_id or not url:
            raise SystemExit(f"{args.stories}: empty story_id or url")
        if story_id in story_to_url:
            raise SystemExit(f"{args.stories}: duplicate story_id {story_id!r}")
        story_to_url[story_id] = url

    host_languages, aliases = _read_host_language(Path(args.host_language))

    pending: list[tuple[dict, str | None, list[str], str, object]] = []
    missing_hosts: set[str] = set()
    for pair, label in labels.items():
        query_id, engine = pair
        if query_id not in query_to_story:
            raise SystemExit(
                f"query_id {query_id!r} is not in {args.queries}"
            )
        story_id = query_to_story[query_id]
        if story_id not in story_to_url:
            raise SystemExit(
                f"story_id {story_id!r} is not in {args.stories}"
            )
        original_host = canonical_host(story_to_url[story_id])
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

        hosts: list[str] = []
        for citation in answer.get("citations") or []:
            host = citation.get("host")
            if not host:
                host = canonical_host(citation.get("url"))
            if host is None:
                missing_hosts.add(f"<no-host: {citation.get('url')}>")
                continue
            hosts.append(host)
            if host not in host_languages:
                missing_hosts.add(host)
        pending.append(
            (label, fact_present, hosts, original_host, answer.get("retrieved"))
        )

    if missing_hosts:
        print(
            f"Hosts absent from {args.host_language}; no codes were assigned:",
            file=sys.stderr,
        )
        for host in sorted(missing_hosts):
            print(f"  {host}", file=sys.stderr)
        return 4

    rows: list[dict] = []
    for label, fact_present, hosts, original_host, retrieved in pending:
        code, d_split, en_also_cited = _assign(
            fact_present,
            hosts,
            original_host,
            host_languages,
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
