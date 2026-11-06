"""Validate the private sample sheet before a real run (D57).

Exits with code 4 and lists every problem when the sample is not complete:
duplicate story ids, unknown outlets, dates outside the fixed window,
invalid origin criteria, PENDING or empty fact keys, outlets with fewer than
three stories that are not declared exceptions, and too many stories from
the same saga for one outlet.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_OUTLETS = Path("data/private/outlets.csv")
DEFAULT_EXCEPTIONS = Path("data/private/sample_exceptions.csv")
DEFAULT_QUERIES = Path("data/private/queries.csv")
WINDOW_START = date(2025, 11, 16)
WINDOW_END = date(2026, 8, 16)
ORIGIN_CRITERIA = {
    "attributed_exclusive",
    "own_investigation",
    "first_published",
}
MIN_STORIES_PER_OUTLET = 3
MAX_STORIES_PER_SAGA = 2
PENDING = "PENDING"
REQUIRED_STORY_COLUMNS = {
    "story_id",
    "outlet_id",
    "country",
    "url",
    "published_at",
    "origin_criterion",
    "origin_evidence",
    "fact_key",
    "english_replica_url",
}
QUERY_COLUMNS = ["query_id", "story_id", "lang", "type", "text"]
QUERY_LANGS = {"es", "en"}
QUERY_TYPES = {"direct", "open"}
EXPECTED_KIND = {"direct": "d", "open": "o"}
QUERY_COMBOS = (
    ("es", "direct"),
    ("es", "open"),
    ("en", "direct"),
    ("en", "open"),
)
QUERY_ID_RE = re.compile(r"^(?P<story>[^-]+)-(?P<lang>es|en)-(?P<kind>[do])$")
EXIT_PROBLEMS = 4


def parse_published_at(value: str | None) -> date | None:
    """Parse a date-only value or an ISO 8601 timestamp; None if invalid."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            return date.fromisoformat(raw)
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _load(path: Path, required: set[str]) -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    if not path.exists():
        return [], [f"{path}: file not found"]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return [], [f"{path}: empty CSV or missing header"]
        missing = required - set(reader.fieldnames)
        if missing:
            problems.append(
                f"{path}: missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        return list(reader), problems


def _header(path: Path) -> list[str] | None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle), None)


def validate(
    stories_path: Path = DEFAULT_STORIES,
    outlets_path: Path = DEFAULT_OUTLETS,
    exceptions_path: Path = DEFAULT_EXCEPTIONS,
    queries_path: Path = DEFAULT_QUERIES,
) -> tuple[list[str], list[str]]:
    """Return ``(problems, warnings)`` for the private sample sheet."""
    problems: list[str] = []
    warnings: list[str] = []

    outlets, outlet_problems = _load(outlets_path, {"outlet_id"})
    problems.extend(outlet_problems)
    outlet_ids = {
        (row.get("outlet_id") or "").strip()
        for row in outlets
        if (row.get("outlet_id") or "").strip()
    }

    exceptions: set[str] = set()
    if exceptions_path.exists():
        exception_rows, exception_problems = _load(exceptions_path, {"outlet_id"})
        problems.extend(exception_problems)
        exceptions = {
            (row.get("outlet_id") or "").strip()
            for row in exception_rows
            if (row.get("outlet_id") or "").strip()
        }
    else:
        warnings.append(
            f"{exceptions_path}: file not found; no exceptions applied"
        )

    stories, story_problems = _load(stories_path, REQUIRED_STORY_COLUMNS)
    problems.extend(story_problems)
    if stories and "saga" not in stories[0].keys():
        problems.append(f"{stories_path}: saga column absent")

    seen_ids: set[str] = set()
    counts: Counter[str] = Counter()
    saga_counts: Counter[tuple[str, str]] = Counter()

    for line_no, row in enumerate(stories, start=2):
        story_id = (row.get("story_id") or "").strip()
        outlet_id = (row.get("outlet_id") or "").strip()
        label = f"{stories_path}:{line_no}"

        for column, value in row.items():
            if value is not None and value.strip() == PENDING:
                problems.append(f"{label}: {column} is PENDING")

        if not story_id:
            problems.append(f"{label}: empty story_id")
        elif story_id in seen_ids:
            problems.append(f"{label}: duplicate story_id {story_id!r}")
        else:
            seen_ids.add(story_id)

        if not outlet_id:
            problems.append(f"{label}: empty outlet_id")
        else:
            counts[outlet_id] += 1
            if outlet_ids and outlet_id not in outlet_ids:
                problems.append(
                    f"{label}: outlet_id {outlet_id!r} is not in "
                    f"{outlets_path}"
                )

        published_at = (row.get("published_at") or "").strip()
        if published_at and published_at != PENDING:
            parsed = parse_published_at(published_at)
            if parsed is None:
                problems.append(
                    f"{label}: published_at {published_at!r} is not a valid "
                    "ISO 8601 date"
                )
            elif not WINDOW_START <= parsed <= WINDOW_END:
                problems.append(
                    f"{label}: published_at {published_at!r} is outside "
                    f"{WINDOW_START.isoformat()}..{WINDOW_END.isoformat()}"
                )
        elif not published_at:
            problems.append(f"{label}: empty published_at")

        criterion = (row.get("origin_criterion") or "").strip()
        if criterion not in ORIGIN_CRITERIA:
            problems.append(
                f"{label}: origin_criterion {criterion!r} is not in "
                f"{sorted(ORIGIN_CRITERIA)}"
            )

        if not (row.get("fact_key") or "").strip():
            problems.append(f"{label}: empty fact_key")

        saga = (row.get("saga") or "").strip()
        if saga and outlet_id:
            saga_counts[(outlet_id, saga)] += 1

    for (outlet_id, saga), count in sorted(saga_counts.items()):
        if count > MAX_STORIES_PER_SAGA:
            problems.append(
                f"{stories_path}: outlet_id {outlet_id!r} has {count} "
                f"stories for saga {saga!r}; more than "
                f"{MAX_STORIES_PER_SAGA}"
            )

    for outlet_id in sorted(outlet_ids):
        count = counts.get(outlet_id, 0)
        if count < MIN_STORIES_PER_OUTLET and outlet_id not in exceptions:
            unit = "story" if count == 1 else "stories"
            problems.append(
                f"{stories_path}: outlet_id {outlet_id!r} has {count} "
                f"{unit}; fewer than {MIN_STORIES_PER_OUTLET} and not in "
                f"{exceptions_path}"
            )

    queries, query_problems = _load(queries_path, set(QUERY_COLUMNS))
    problems.extend(query_problems)
    if queries_path.exists():
        header = _header(queries_path)
        if header != QUERY_COLUMNS:
            got = ",".join(header or [])
            problems.append(
                f"{queries_path}: header must be "
                f"{','.join(QUERY_COLUMNS)}, got {got}"
            )

    query_ids: set[str] = set()
    text_to_ids: dict[str, list[str]] = defaultdict(list)
    combos: Counter[tuple[str, str, str]] = Counter()

    for line_no, row in enumerate(queries, start=2):
        label = f"{queries_path}:{line_no}"
        query_id = (row.get("query_id") or "").strip()
        story_id = (row.get("story_id") or "").strip()
        lang = (row.get("lang") or "").strip()
        qtype = (row.get("type") or "").strip()
        text = (row.get("text") or "").strip()
        match = QUERY_ID_RE.match(query_id) if query_id else None

        if not query_id:
            problems.append(f"{label}: empty query_id")
        elif query_id in query_ids:
            problems.append(f"{label}: duplicate query_id {query_id!r}")
        else:
            query_ids.add(query_id)
            if match is None:
                problems.append(
                    f"{label}: query_id {query_id!r} does not match "
                    "<story_id>-<es|en>-<d|o>"
                )
            elif match.group("story") != story_id:
                problems.append(
                    f"{label}: story_id {story_id!r} does not match "
                    f"query_id {query_id!r}"
                )

        if not story_id:
            problems.append(f"{label}: empty story_id")
        elif story_id not in seen_ids:
            problems.append(
                f"{label}: story_id {story_id!r} is not in {stories_path}"
            )

        if lang not in QUERY_LANGS:
            problems.append(
                f"{label}: lang {lang!r} is not in {sorted(QUERY_LANGS)}"
            )
        elif match is not None and match.group("lang") != lang:
            problems.append(
                f"{label}: lang {lang!r} does not match query_id {query_id!r}"
            )

        if qtype not in QUERY_TYPES:
            problems.append(
                f"{label}: type {qtype!r} is not in {sorted(QUERY_TYPES)}"
            )
        elif match is not None and EXPECTED_KIND[qtype] != match.group("kind"):
            problems.append(
                f"{label}: type {qtype!r} does not match query_id {query_id!r}"
            )

        if not text:
            problems.append(f"{label}: empty text")
        elif text == PENDING:
            problems.append(f"{label}: text is PENDING")
        else:
            text_to_ids[text].append(query_id or label)

        if story_id and lang in QUERY_LANGS and qtype in QUERY_TYPES:
            combos[(story_id, lang, qtype)] += 1

    for ids in text_to_ids.values():
        if len(ids) > 1:
            problems.append(
                f"{queries_path}: repeated query text in {', '.join(ids)}"
            )

    for story_id in sorted(seen_ids):
        for lang, qtype in QUERY_COMBOS:
            expected_id = f"{story_id}-{lang}-{EXPECTED_KIND[qtype]}"
            count = combos.get((story_id, lang, qtype), 0)
            if count == 0:
                problems.append(f"{queries_path}: missing query {expected_id}")
            elif count > 1:
                problems.append(
                    f"{queries_path}: duplicate query {expected_id} "
                    f"({count} rows)"
                )

    return problems, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the private sample sheet before a real run."
    )
    parser.add_argument("--stories", default=str(DEFAULT_STORIES))
    parser.add_argument("--outlets", default=str(DEFAULT_OUTLETS))
    parser.add_argument("--exceptions", default=str(DEFAULT_EXCEPTIONS))
    parser.add_argument("--queries", default=str(DEFAULT_QUERIES))
    args = parser.parse_args(argv)

    problems, warnings = validate(
        Path(args.stories),
        Path(args.outlets),
        Path(args.exceptions),
        Path(args.queries),
    )
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if problems:
        print(f"Sample validation failed: {len(problems)} problem(s)")
        for problem in problems:
            print(problem)
        return EXIT_PROBLEMS
    print("Sample validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
