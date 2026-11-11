"""Aggregate metrics with a story-cluster bootstrap (D74, D79).

Counts and rates follow METHOD §7. Every rate has a null value when its
denominator is 0, is bounded by 0 and 1, and carries a 95% percentile
interval from resampling stories with replacement. The attribution gap is
bounded by -1 and 1.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

from src import adjudicate

DEFAULT_LABELS = Path("data/private/labels.csv")
DEFAULT_QUERIES = Path("data/private/queries.csv")
DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_REVIEW_DIR = Path("data/private")
DEFAULT_RESULTS_DIR = Path("results")

ENGINES = ("gemini", "openai", "claude", "perplexity")
TYPES = ("direct", "open")
LANGS = ("es", "en")
CODES = ("A", "B", "C", "D", "E")
FACT_VALUES = {"yes", "no", "wrong"}
ACCESS_COLUMNS = {
    "gemini": "engine_gemini",
    "gemini_with_extended": "engine_gemini_with_extended",
    "openai": "engine_openai",
    "claude": "engine_claude",
    "perplexity": "engine_perplexity",
}
ACCESS_VALUES = {"open", "closed"}

RESAMPLES = 2000
SEED = 20261109
PERCENTILES = (2.5, 97.5)

MAIN_RATES = (
    "fact_presence",
    "original_attribution",
    "es_displacement",
    "en_displacement",
    "no_classified",
    "d_none",
    "d_other",
)
COUNT_FIELDS = (
    "n",
    "a",
    "b",
    "c",
    "d_none",
    "d_other",
    "e",
    "p",
    "fnc",
    "cited",
    "p_es",
    "a_es",
    "p_en",
    "a_en",
)
FOUND_NOT_CITED_ENGINES = ("openai", "claude")
STORY_CITED_ENGINES = ("openai", "claude", "perplexity")


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


def _load_queries(path: Path) -> dict[str, dict]:
    queries: dict[str, dict] = {}
    for row in _read_csv(path, {"query_id", "story_id", "lang", "type"}):
        query_id = (row.get("query_id") or "").strip()
        if not query_id or query_id in queries:
            raise SystemExit(f"{path}: empty or duplicate query_id {query_id!r}")
        lang = (row.get("lang") or "").strip()
        question_type = (row.get("type") or "").strip()
        if lang not in LANGS:
            raise SystemExit(f"{path}: query {query_id!r} has lang {lang!r}")
        if question_type not in TYPES:
            raise SystemExit(
                f"{path}: query {query_id!r} has type {question_type!r}"
            )
        queries[query_id] = {
            "story_id": (row.get("story_id") or "").strip(),
            "lang": lang,
            "type": question_type,
        }
    return queries


def _load_stories(path: Path) -> dict[str, dict]:
    stories: dict[str, dict] = {}
    for row in _read_csv(
        path, {"story_id", "outlet_id", "country", "english_replica_url"}
    ):
        story_id = (row.get("story_id") or "").strip()
        if not story_id or story_id in stories:
            raise SystemExit(f"{path}: empty or duplicate story_id {story_id!r}")
        outlet_id = (row.get("outlet_id") or "").strip()
        country = (row.get("country") or "").strip()
        if not outlet_id or not country:
            raise SystemExit(f"{path}: story {story_id!r} lacks outlet or country")
        replica = (row.get("english_replica_url") or "").strip()
        stories[story_id] = {
            "outlet_id": outlet_id,
            "country": country,
            "english_replica_url": replica,
        }
    return stories


def _load_access(path: Path) -> dict[str, dict]:
    required = {"story_id", *ACCESS_COLUMNS.values()}
    access: dict[str, dict] = {}
    for row in _read_csv(path, required):
        story_id = (row.get("story_id") or "").strip()
        if not story_id or story_id in access:
            raise SystemExit(f"{path}: empty or duplicate story_id {story_id!r}")
        statuses = {}
        for column in ACCESS_COLUMNS.values():
            value = (row.get(column) or "").strip()
            if value not in ACCESS_VALUES:
                raise SystemExit(
                    f"{path}: story {story_id!r} has {column}={value!r}"
                )
            statuses[column] = value
        access[story_id] = statuses
    return access


def load_rows(labels_path: Path, queries_path: Path, stories_path: Path) -> list[dict]:
    """Join labels with query and story attributes, validating consistency."""
    queries = _load_queries(queries_path)
    stories = _load_stories(stories_path)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in _read_csv(
        labels_path,
        {
            "query_id",
            "engine",
            "fact_present",
            "code",
            "d_split",
            "original_in_retrieved",
            "original_url_cited",
        },
    ):
        query_id = (row.get("query_id") or "").strip()
        engine = (row.get("engine") or "").strip()
        if engine not in ENGINES:
            raise SystemExit(f"{labels_path}: unknown engine {engine!r}")
        pair = (query_id, engine)
        if pair in seen:
            raise SystemExit(
                f"{labels_path}: duplicate pair {query_id!r}/{engine!r}"
            )
        seen.add(pair)
        query = queries.get(query_id)
        if query is None:
            raise SystemExit(f"{labels_path}: query_id {query_id!r} unknown")
        story = stories.get(query["story_id"])
        if story is None:
            raise SystemExit(
                f"{labels_path}: story_id {query['story_id']!r} unknown"
            )
        fact = (row.get("fact_present") or "").strip()
        code = (row.get("code") or "").strip()
        if fact not in FACT_VALUES:
            raise SystemExit(
                f"{labels_path}: {query_id!r}/{engine!r} fact {fact!r}"
            )
        if code not in CODES:
            raise SystemExit(
                f"{labels_path}: {query_id!r}/{engine!r} code {code!r}"
            )
        if (fact == "yes") != (code in ("A", "B", "C", "D")):
            raise SystemExit(
                f"{labels_path}: {query_id!r}/{engine!r} fact/code mismatch"
            )
        d_split = (row.get("d_split") or "").strip()
        if code == "D" and d_split not in ("none", "other"):
            raise SystemExit(
                f"{labels_path}: {query_id!r}/{engine!r} d_split {d_split!r}"
            )
        rows.append(
            {
                "query_id": query_id,
                "engine": engine,
                "story_id": query["story_id"],
                "lang": query["lang"],
                "type": query["type"],
                "country": story["country"],
                "outlet_id": story["outlet_id"],
                "english_replica_url": story["english_replica_url"],
                "fact_present": fact,
                "code": code,
                "d_split": d_split,
                "original_in_retrieved": (
                    row.get("original_in_retrieved") or ""
                ).strip(),
                "original_url_cited": (
                    row.get("original_url_cited") or ""
                ).strip(),
            }
        )
    return rows


def _zero() -> dict[str, int]:
    return dict.fromkeys(COUNT_FIELDS, 0)


def _add(total: dict[str, int], cell: dict[str, int]) -> None:
    for field in COUNT_FIELDS:
        total[field] += cell[field]


def _counts(rows: list[dict]) -> dict[str, int]:
    counts = _zero()
    for row in rows:
        counts["n"] += 1
        code = row["code"]
        if code == "A":
            counts["a"] += 1
        elif code == "B":
            counts["b"] += 1
        elif code == "C":
            counts["c"] += 1
        elif code == "D":
            if row["d_split"] == "none":
                counts["d_none"] += 1
            elif row["d_split"] == "other":
                counts["d_other"] += 1
            else:
                raise ValueError(f"invalid d_split {row['d_split']!r}")
        elif code == "E":
            counts["e"] += 1
        else:
            raise ValueError(f"invalid code {code!r}")
        if code in ("A", "B", "C", "D"):
            counts["p"] += 1
        if (
            row["fact_present"] == "yes"
            and row["original_in_retrieved"] == "true"
            and code != "A"
        ):
            counts["fnc"] += 1
        if row["original_url_cited"] == "true":
            counts["cited"] += 1
        if row["lang"] == "es":
            if code in ("A", "B", "C", "D"):
                counts["p_es"] += 1
            if code == "A":
                counts["a_es"] += 1
        elif row["lang"] == "en":
            if code in ("A", "B", "C", "D"):
                counts["p_en"] += 1
            if code == "A":
                counts["a_en"] += 1
        else:
            raise ValueError(f"invalid lang {row['lang']!r}")
    return counts


def _rate(value: float, name: str, low: float = 0.0, high: float = 1.0):
    if not (low <= value <= high):
        raise ValueError(f"{name} out of range [{low}, {high}]: {value}")
    return value


def _ratio(numerator: int, denominator: int, name: str):
    if denominator == 0:
        return None
    return _rate(numerator / denominator, name)


def _gap(counts: dict[str, int]):
    if counts["p_es"] == 0 or counts["p_en"] == 0:
        return None
    value = counts["a_es"] / counts["p_es"] - counts["a_en"] / counts["p_en"]
    return _rate(value, "attribution_gap", -1.0, 1.0)


METRIC_VALUES = {
    "fact_presence": lambda c: _ratio(c["p"], c["n"], "fact_presence"),
    "original_attribution": lambda c: _ratio(
        c["a"], c["p"], "original_attribution"
    ),
    "es_displacement": lambda c: _ratio(c["b"], c["p"], "es_displacement"),
    "en_displacement": lambda c: _ratio(c["c"], c["p"], "en_displacement"),
    "no_classified": lambda c: _ratio(c["d_none"] + c["d_other"], c["p"], "no_classified"),
    "d_none": lambda c: _ratio(c["d_none"], c["p"], "d_none"),
    "d_other": lambda c: _ratio(c["d_other"], c["p"], "d_other"),
    "attribution_gap": _gap,
    "found_not_cited": lambda c: _ratio(c["fnc"], c["p"], "found_not_cited"),
    "story_cited": lambda c: _ratio(c["cited"], c["p"], "story_cited"),
}


def _cells(rows: list[dict]) -> dict[str, dict[str, int]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["story_id"]].append(row)
    return {story_id: _counts(group) for story_id, group in grouped.items()}


def _percentile(ordered: list[float], pct: float):
    count = len(ordered)
    if count == 0:
        return None
    if count == 1:
        return ordered[0]
    position = (count - 1) * pct / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _interval(values: list[float]) -> dict:
    if not values:
        return {"ci_low": None, "ci_high": None, "n_valid": 0}
    ordered = sorted(values)
    low, high = PERCENTILES
    return {
        "ci_low": _percentile(ordered, low),
        "ci_high": _percentile(ordered, high),
        "n_valid": len(ordered),
    }


def bootstrap_all(
    cells: dict[str, dict[str, int]],
    names,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict[str, dict]:
    """Resample stories with replacement and return percentile intervals.

    A resample whose denominator is 0 is discarded; ``n_valid`` counts the
    resamples that yielded a value.
    """
    story_ids = sorted(cells)
    results: dict[str, list[float]] = {name: [] for name in names}
    if not story_ids:
        return {
            name: {"ci_low": None, "ci_high": None, "n_valid": 0}
            for name in names
        }
    rng = random.Random(seed)
    size = len(story_ids)
    for _ in range(resamples):
        total = _zero()
        for story_id in rng.choices(story_ids, k=size):
            _add(total, cells[story_id])
        for name in names:
            value = METRIC_VALUES[name](total)
            if value is not None:
                results[name].append(value)
    return {name: _interval(values) for name, values in results.items()}


def summarize(
    rows: list[dict],
    names=MAIN_RATES,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> dict:
    counts = _counts(rows)
    intervals = bootstrap_all(_cells(rows), names, resamples, seed)
    payload = {
        "N": counts["n"],
        "A": counts["a"],
        "B": counts["b"],
        "C": counts["c"],
        "D": counts["d_none"] + counts["d_other"],
        "D_none": counts["d_none"],
        "D_other": counts["d_other"],
        "E": counts["e"],
        "P": counts["p"],
    }
    for name in names:
        interval = intervals[name]
        payload[name] = {
            "value": METRIC_VALUES[name](counts),
            "ci_low": interval["ci_low"],
            "ci_high": interval["ci_high"],
            "n_valid": interval["n_valid"],
        }
    return payload


def _subset(rows: list[dict], predicate) -> list[dict]:
    return [row for row in rows if predicate(row)]


def _by_type(rows: list[dict]) -> dict:
    return {
        question_type: summarize(_subset(rows, lambda r, t=question_type: r["type"] == t))
        for question_type in TYPES
    }


def _by_engine(rows: list[dict]) -> dict:
    return {
        engine: summarize(_subset(rows, lambda r, e=engine: r["engine"] == e))
        for engine in ENGINES
    }


def _by_country(rows: list[dict]) -> dict:
    countries = sorted({row["country"] for row in rows})
    return {
        country: summarize(_subset(rows, lambda r, c=country: r["country"] == c))
        for country in countries
    }


def _by_lang(rows: list[dict]) -> dict:
    return {
        lang: summarize(_subset(rows, lambda r, l=lang: r["lang"] == l))
        for lang in LANGS
    }


def _by_type_lang(rows: list[dict]) -> dict:
    return {
        question_type: {
            lang: summarize(
                _subset(
                    rows,
                    lambda r, t=question_type, l=lang: r["type"] == t
                    and r["lang"] == l,
                )
            )
            for lang in LANGS
        }
        for question_type in TYPES
    }


def _by_type_engine(rows: list[dict]) -> dict:
    return {
        question_type: {
            engine: summarize(
                _subset(
                    rows,
                    lambda r, t=question_type, e=engine: r["type"] == t
                    and r["engine"] == e,
                )
            )
            for engine in ENGINES
        }
        for question_type in TYPES
    }


def _by_replica(rows: list[dict]) -> dict:
    def has_replica(row: dict) -> bool:
        value = row["english_replica_url"]
        return bool(value) and value != "PENDING"

    return {
        "with_english_replica": summarize(_subset(rows, has_replica)),
        "without_english_replica": summarize(
            _subset(rows, lambda row: not has_replica(row))
        ),
    }


def _by_engine_access(rows: list[dict], access: dict[str, dict]) -> dict:
    result = {}
    for group, column in ACCESS_COLUMNS.items():
        engine = "gemini" if group == "gemini_with_extended" else group
        engine_rows = _subset(rows, lambda r, e=engine: r["engine"] == e)
        result[group] = {
            status: summarize(
                _subset(
                    engine_rows,
                    lambda r, c=column, s=status: access[r["story_id"]][c] == s,
                )
            )
            for status in ("open", "closed")
        }
    return result


def _gap_cut(rows: list[dict]) -> dict:
    return summarize(rows, names=("attribution_gap",))["attribution_gap"]


def _secondary(rows: list[dict]) -> dict:
    found = {}
    for engine in FOUND_NOT_CITED_ENGINES:
        engine_rows = _subset(rows, lambda r, e=engine: r["engine"] == e)
        found[engine] = summarize(
            engine_rows, names=("found_not_cited",)
        )["found_not_cited"]
    cited = {}
    for engine in STORY_CITED_ENGINES:
        engine_rows = _subset(rows, lambda r, e=engine: r["engine"] == e)
        cited[engine] = summarize(engine_rows, names=("story_cited",))[
            "story_cited"
        ]
    return {"found_not_cited": found, "story_cited": cited}


def _latest_labels(path: Path) -> dict[tuple[str, str], str]:
    latest: dict[tuple[str, str], str] = {}
    for record in _read_jsonl(path):
        fact = record.get("fact_present")
        if fact not in FACT_VALUES:
            raise SystemExit(
                f"{path}: invalid fact_present {fact!r} for "
                f"{record.get('query_id')!r}/{record.get('engine')!r}"
            )
        latest[(record.get("query_id"), record.get("engine"))] = fact
    return latest


def _validation(
    judge1_path: Path,
    judge2_path: Path,
    review_path: Path,
    key_path: Path,
    rounding_path: Path,
) -> dict:
    judge1 = _latest_labels(judge1_path)
    judge2 = _latest_labels(judge2_path)
    if set(judge1) != set(judge2):
        raise SystemExit(
            "STOP: the two judges do not cover the same pairs."
        )
    pairs = sorted(judge1)
    labelled = [(judge1[pair], judge2[pair]) for pair in pairs]

    review = {
        (row.get("review_id") or "").strip(): row
        for row in _read_csv(review_path, {"review_id", "human_fact_present"})
    }
    keys = _read_csv(
        key_path, {"review_id", "judge1", "set"}
    )
    disagreements = sum(1 for row in keys if row["set"] == "disagreement")
    controls = [row for row in keys if row["set"] == "agreement_check"]
    corrected = 0
    for row in controls:
        human = (review.get((row.get("review_id") or "").strip()) or {}).get(
            "human_fact_present"
        )
        if human not in FACT_VALUES:
            raise SystemExit(
                "STOP: review row lacks a valid human_fact_present."
            )
        if row["judge1"] != human:
            corrected += 1

    checked = 0
    changed = 0
    nulls = 0
    for record in _read_jsonl(rounding_path):
        checked += 1
        if record.get("check_output") == "yes":
            changed += 1
        elif record.get("check_output") is None:
            nulls += 1

    return {
        "judge_agreement": adjudicate.simple_agreement(labelled),
        "judge_kappa": adjudicate.cohen_kappa(labelled),
        "reviewed_rows": len(review),
        "disagreements": disagreements,
        "agreement_checks": len(controls),
        "agreement_checks_corrected": corrected,
        "rounding_checked": checked,
        "rounding_changed": changed,
        "rounding_null": nulls,
    }


def _costs(paths: list[Path]) -> dict:
    by_file = {}
    total = 0.0
    known_total = 0
    unknown_total = 0
    for path in paths:
        if not path.exists():
            raise SystemExit(f"cost file not found: {path}")
        known = 0
        unknown = 0
        subtotal = 0.0
        for record in _read_jsonl(path):
            value = record.get("cost_usd")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                subtotal += float(value)
                known += 1
            else:
                unknown += 1
        by_file[path.name] = {
            "total_usd": round(subtotal, 6),
            "known_records": known,
            "unknown_records": unknown,
        }
        total += subtotal
        known_total += known
        unknown_total += unknown
    return {
        "total_usd": round(total, 6),
        "known_records": known_total,
        "unknown_records": unknown_total,
        "by_file": by_file,
    }


def _write_outlet_csv(path: Path, rows: list[dict]) -> None:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["outlet_id"]].append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["outlet_id", "N", "P", "A", "A/P", "E"]
        )
        writer.writeheader()
        for outlet_id in sorted(grouped):
            counts = _counts(grouped[outlet_id])
            ratio = _ratio(counts["a"], counts["p"], "original_attribution")
            writer.writerow(
                {
                    "outlet_id": outlet_id,
                    "N": counts["n"],
                    "P": counts["p"],
                    "A": counts["a"],
                    "A/P": "" if ratio is None else f"{ratio:.6f}",
                    "E": counts["e"],
                }
            )


def _fmt_value(value) -> str:
    return "null" if value is None else f"{value:.6f}"


def _fmt_metric(entry: dict) -> str:
    return (
        f"{_fmt_value(entry['value'])} "
        f"[{_fmt_value(entry['ci_low'])}, {_fmt_value(entry['ci_high'])}] "
        f"(n_valid {entry['n_valid']})"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate metrics with a story-cluster bootstrap."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--labels", default=str(DEFAULT_LABELS))
    parser.add_argument("--queries", default=str(DEFAULT_QUERIES))
    parser.add_argument("--stories", default=str(DEFAULT_STORIES))
    parser.add_argument("--access", default=None)
    parser.add_argument("--answers", default=None)
    parser.add_argument("--labels-raw", default=None)
    parser.add_argument("--labels-raw-judge2", default=None)
    parser.add_argument("--review", default=None)
    parser.add_argument("--review-key", default=None)
    parser.add_argument("--rounding-check", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--out-outlets", default=None)
    parser.add_argument(
        "--resamples", type=int, default=RESAMPLES, help="bootstrap resamples"
    )
    args = parser.parse_args(argv)

    run_dir = DEFAULT_RUNS_DIR / args.run_id
    access_path = (
        Path(args.access)
        if args.access
        else Path(f"data/private/access_{args.run_id}.csv")
    )
    answers_path = (
        Path(args.answers) if args.answers else run_dir / "answers.jsonl"
    )
    judge1_path = (
        Path(args.labels_raw)
        if args.labels_raw
        else run_dir / "labels_raw.jsonl"
    )
    judge2_path = (
        Path(args.labels_raw_judge2)
        if args.labels_raw_judge2
        else run_dir / "labels_raw_judge2.jsonl"
    )
    review_path = (
        Path(args.review)
        if args.review
        else DEFAULT_REVIEW_DIR / f"review_{args.run_id}.csv"
    )
    key_path = (
        Path(args.review_key)
        if args.review_key
        else DEFAULT_REVIEW_DIR / f"review_key_{args.run_id}.csv"
    )
    rounding_path = (
        Path(args.rounding_check)
        if args.rounding_check
        else run_dir / "rounding_check.jsonl"
    )
    out_path = (
        Path(args.out)
        if args.out
        else DEFAULT_RESULTS_DIR / f"metrics_{args.run_id}.json"
    )
    outlet_path = (
        Path(args.out_outlets)
        if args.out_outlets
        else DEFAULT_REVIEW_DIR / f"metrics_by_outlet_{args.run_id}.csv"
    )

    rows = load_rows(Path(args.labels), Path(args.queries), Path(args.stories))
    access = _load_access(access_path)
    missing_access = sorted({row["story_id"] for row in rows} - set(access))
    if missing_access:
        raise SystemExit(
            "STOP: stories missing from the access table: "
            + ", ".join(missing_access)
        )

    cuts = {
        "total": summarize(rows, resamples=args.resamples),
        "by_type": _by_type(rows),
        "by_engine": _by_engine(rows),
        "by_country": _by_country(rows),
        "by_lang": _by_lang(rows),
        "by_type_lang": _by_type_lang(rows),
        "by_type_engine": _by_type_engine(rows),
        "by_replica": _by_replica(rows),
        "by_engine_access": _by_engine_access(rows, access),
    }
    gaps = {
        "total": _gap_cut(rows),
        "direct": _gap_cut(_subset(rows, lambda r: r["type"] == "direct")),
        "open": _gap_cut(_subset(rows, lambda r: r["type"] == "open")),
        "direct_by_engine": {
            engine: _gap_cut(
                _subset(
                    rows,
                    lambda r, e=engine: r["type"] == "direct"
                    and r["engine"] == e,
                )
            )
            for engine in ENGINES
        },
    }
    payload = {
        "run_id": args.run_id,
        "bootstrap": {
            "resamples": args.resamples,
            "seed": SEED,
            "percentiles": list(PERCENTILES),
        },
        "cuts": cuts,
        "gaps": gaps,
        "secondary": _secondary(rows),
        "validation": _validation(
            judge1_path, judge2_path, review_path, key_path, rounding_path
        ),
        "costs": _costs(
            [answers_path, judge1_path, judge2_path, rounding_path]
        ),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    _write_outlet_csv(outlet_path, rows)

    direct = cuts["by_type"]["direct"]
    print(f"A/P direct: {_fmt_metric(direct['original_attribution'])}")
    print(f"Attribution gap direct (es - en): {_fmt_metric(gaps['direct'])}")
    print(
        "C/P direct by lang: es "
        f"{_fmt_value(cuts['by_type_lang']['direct']['es']['en_displacement']['value'])}; "
        "en "
        f"{_fmt_value(cuts['by_type_lang']['direct']['en']['en_displacement']['value'])}"
    )
    print(
        "A/P by engine direct: "
        + "; ".join(
            f"{engine} "
            f"{_fmt_value(cuts['by_type_engine']['direct'][engine]['original_attribution']['value'])}"
            for engine in ENGINES
        )
    )
    print(f"Wrote {out_path} and {outlet_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
