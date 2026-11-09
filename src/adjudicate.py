"""Two-judge adjudication: blind review sheet and final labels (D73, D75)."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from src.classify import PROMPT_VERSION

DEFAULT_QUERIES = Path("data/private/queries.csv")
DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_REVIEW_DIR = Path("data/private")
JUDGE1_FILE = "labels_raw.jsonl"
JUDGE2_FILE = "labels_raw_judge2.jsonl"
CATEGORIES = ("yes", "no", "wrong")
SAMPLE_SEED = 20261109
AGREEMENT_CHECK_N = 30
REVIEW_FIELDS = [
    "review_id",
    "fact_key",
    "query_text",
    "answer_text",
    "human_fact_present",
]
KEY_FIELDS = [
    "review_id",
    "query_id",
    "engine",
    "judge1",
    "judge2",
    "set",
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


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _latest_labels(path: Path) -> dict:
    latest: dict = {}
    for record in _read_jsonl(path):
        latest[(record.get("query_id"), record.get("engine"))] = record
    return latest


def _load_queries(path: Path) -> dict[str, dict]:
    queries: dict[str, dict] = {}
    for row in _read_csv(path, {"query_id", "story_id", "text"}):
        query_id = (row.get("query_id") or "").strip()
        if not query_id or query_id in queries:
            raise SystemExit(f"{path}: empty or duplicate query_id {query_id!r}")
        queries[query_id] = {
            "story_id": (row.get("story_id") or "").strip(),
            "query_text": row.get("text") or "",
        }
    return queries


def _load_facts(path: Path) -> dict[str, str]:
    facts: dict[str, str] = {}
    for row in _read_csv(path, {"story_id", "fact_key"}):
        story_id = (row.get("story_id") or "").strip()
        if not story_id or story_id in facts:
            raise SystemExit(f"{path}: empty or duplicate story_id {story_id!r}")
        facts[story_id] = (row.get("fact_key") or "").strip()
    return facts


def _load_answers(path: Path) -> dict:
    latest: dict = {}
    for record in _read_jsonl(path):
        if not record.get("error"):
            latest[(record.get("query_id"), record.get("engine"))] = record
    return latest


def simple_agreement(rows: list[tuple[str, str]]) -> float | None:
    if not rows:
        return None
    return sum(1 for left, right in rows if left == right) / len(rows)


def cohen_kappa(rows: list[tuple[str, str]]) -> float | None:
    """Unweighted Cohen's kappa; null when fewer than two categories appear."""
    if not rows:
        return None
    categories = {left for left, _ in rows} | {right for _, right in rows}
    if len(categories) < 2:
        return None
    n = len(rows)
    observed = sum(1 for left, right in rows if left == right) / n
    expected = sum(
        (sum(1 for left, _ in rows if left == category) / n)
        * (sum(1 for _, right in rows if right == category) / n)
        for category in categories
    )
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def _cross_table(rows: list[tuple[str, str]]) -> dict:
    table = {
        left: {right: 0 for right in CATEGORIES} for left in CATEGORIES
    }
    for left, right in rows:
        table[left][right] += 1
    return table


def _print_cross_table(rows: list[tuple[str, str]]) -> None:
    table = _cross_table(rows)
    print("Cross table (judge1 rows, judge2 columns):")
    print(f"{'':>8}" + "".join(f"{c:>8}" for c in CATEGORIES))
    for left in CATEGORIES:
        print(f"{left:>8}" + "".join(f"{table[left][right]:>8}" for right in CATEGORIES))


def build(args: argparse.Namespace) -> int:
    run_dir = DEFAULT_RUNS_DIR / args.run_id
    judge1_path = run_dir / JUDGE1_FILE
    judge2_path = run_dir / JUDGE2_FILE
    for path in (judge1_path, judge2_path):
        if not path.exists():
            raise SystemExit(f"labels file not found: {path}")

    judge1 = _latest_labels(judge1_path)
    judge2 = _latest_labels(judge2_path)

    pairs1 = set(judge1)
    pairs2 = set(judge2)
    if pairs1 != pairs2:
        only1 = sorted(pairs1 - pairs2)
        only2 = sorted(pairs2 - pairs1)
        print(
            "STOP: the two judges do not cover the same pairs "
            f"(judge1 only: {len(only1)}, judge2 only: {len(only2)}).",
            file=sys.stderr,
        )
        for label, pairs in (("judge1 only", only1), ("judge2 only", only2)):
            for query_id, engine in pairs[:10]:
                print(f"  {label}: {query_id}/{engine}", file=sys.stderr)
        return 4

    nulls = [
        (query_id, engine, side)
        for side, labels in (("judge1", judge1), ("judge2", judge2))
        for (query_id, engine), record in sorted(labels.items())
        if record.get("fact_present") not in CATEGORIES
    ]
    if nulls:
        print(
            f"STOP: {len(nulls)} label(s) are null or invalid.",
            file=sys.stderr,
        )
        for query_id, engine, side in nulls:
            print(f"  {side}: {query_id}/{engine}", file=sys.stderr)
        return 4

    pairs = sorted(pairs1)
    rows = [
        (judge1[pair]["fact_present"], judge2[pair]["fact_present"])
        for pair in pairs
    ]
    disagreements = [
        pair
        for pair in pairs
        if judge1[pair]["fact_present"] != judge2[pair]["fact_present"]
    ]
    disagreement_set = set(disagreements)
    agreements = [pair for pair in pairs if pair not in disagreement_set]

    rng = random.Random(SAMPLE_SEED)
    check_n = min(AGREEMENT_CHECK_N, len(agreements))
    checks = rng.sample(sorted(agreements), check_n)

    selected = [(pair, "disagreement") for pair in disagreements] + [
        (pair, "agreement_check") for pair in checks
    ]
    rng.shuffle(selected)

    queries = _load_queries(Path(args.queries))
    facts = _load_facts(Path(args.stories))
    answers = _load_answers(run_dir / "answers.jsonl")

    review_rows: list[dict] = []
    key_rows: list[dict] = []
    problems: list[str] = []
    for index, (pair, kind) in enumerate(selected, start=1):
        query_id, engine = pair
        review_id = f"R-{index:04d}"
        query = queries.get(query_id)
        if query is None:
            problems.append(f"query_id {query_id!r} is not in {args.queries}")
            continue
        fact_key = facts.get(query["story_id"])
        if fact_key is None:
            problems.append(
                f"story_id {query['story_id']!r} is not in {args.stories}"
            )
            continue
        answer = answers.get(pair)
        if answer is None:
            problems.append(f"answer missing for {query_id}/{engine}")
            continue
        review_rows.append(
            {
                "review_id": review_id,
                "fact_key": fact_key,
                "query_text": query["query_text"],
                "answer_text": answer.get("answer_text") or "",
                "human_fact_present": "",
            }
        )
        key_rows.append(
            {
                "review_id": review_id,
                "query_id": query_id,
                "engine": engine,
                "judge1": judge1[pair]["fact_present"],
                "judge2": judge2[pair]["fact_present"],
                "set": kind,
            }
        )

    if problems:
        for problem in problems:
            print(f"STOP: {problem}", file=sys.stderr)
        return 4

    agreement = simple_agreement(rows)
    kappa = cohen_kappa(rows)
    print(f"Pairs: {len(pairs)}")
    if agreement is None:
        print("Agreement: null")
    else:
        matches = sum(1 for left, right in rows if left == right)
        print(f"Agreement: {agreement:.6f} ({matches}/{len(rows)})")
    print("Cohen's kappa: null" if kappa is None else f"Cohen's kappa: {kappa:.6f}")
    _print_cross_table(rows)

    review_path = DEFAULT_REVIEW_DIR / f"review_{args.run_id}.csv"
    key_path = DEFAULT_REVIEW_DIR / f"review_key_{args.run_id}.csv"
    _write_csv(review_path, REVIEW_FIELDS, review_rows)
    _write_csv(key_path, KEY_FIELDS, key_rows)
    print(
        f"Wrote {len(review_rows)} rows to {review_path} "
        f"({len(disagreements)} disagreements + {check_n} agreement checks)"
    )
    return 0


def merge(args: argparse.Namespace) -> int:
    run_dir = DEFAULT_RUNS_DIR / args.run_id
    judge1_path = run_dir / JUDGE1_FILE
    judge2_path = run_dir / JUDGE2_FILE
    review_path = DEFAULT_REVIEW_DIR / f"review_{args.run_id}.csv"
    key_path = DEFAULT_REVIEW_DIR / f"review_key_{args.run_id}.csv"
    for path in (judge1_path, judge2_path, review_path, key_path):
        if not path.exists():
            raise SystemExit(f"file not found: {path}")

    judge1 = _latest_labels(judge1_path)
    judge2 = _latest_labels(judge2_path)
    review = {
        row["review_id"]: row
        for row in _read_csv(review_path, set(REVIEW_FIELDS))
    }
    key = {
        row["review_id"]: row for row in _read_csv(key_path, set(KEY_FIELDS))
    }

    missing = [
        review_id
        for review_id, row in review.items()
        if (row.get("human_fact_present") or "").strip() not in CATEGORIES
    ]
    if missing:
        print(
            f"STOP: {len(missing)} review row(s) lack a valid human_fact_present.",
            file=sys.stderr,
        )
        for review_id in sorted(missing):
            print(f"  {review_id}", file=sys.stderr)
        return 4

    if set(review) != set(key):
        print(
            "STOP: review and key files do not have the same review_ids.",
            file=sys.stderr,
        )
        return 4

    if set(judge1) != set(judge2):
        print(
            "STOP: the two judges do not cover the same pairs.",
            file=sys.stderr,
        )
        return 4

    human: dict = {}
    pair_set: dict = {}
    for review_id, row in key.items():
        pair = (row["query_id"], row["engine"])
        human[pair] = review[review_id]["human_fact_present"].strip()
        pair_set[pair] = row["set"]

    reviewed = list(human.items())
    judge1_rows = [
        (judge1[pair]["fact_present"], label) for pair, label in reviewed
    ]
    judge2_rows = [
        (judge2[pair]["fact_present"], label) for pair, label in reviewed
    ]
    controls = [
        (pair, label)
        for pair, label in reviewed
        if pair_set[pair] == "agreement_check"
    ]
    corrected = sum(
        1
        for pair, label in controls
        if judge1[pair]["fact_present"] != label
    )

    all_pairs = set(judge1) | set(judge2)
    unknown = sorted(set(human) - all_pairs)
    if unknown:
        print(
            "STOP: reviewed pairs are missing from the judge labels.",
            file=sys.stderr,
        )
        for query_id, engine in unknown[:10]:
            print(f"  {query_id}/{engine}", file=sys.stderr)
        return 4

    final_rows: list[dict] = []
    for pair, source in judge1.items():
        agreed = judge1[pair]["fact_present"] == judge2[pair]["fact_present"]
        if pair in human:
            fact_present = human[pair]
        elif agreed:
            fact_present = judge1[pair]["fact_present"]
        else:
            print(
                f"STOP: disagreement for {pair[0]}/{pair[1]} was not reviewed.",
                file=sys.stderr,
            )
            return 4
        record = dict(source)
        record["fact_present"] = fact_present
        record["classifier_model"] = "adjudicated"
        record["prompt_version"] = (
            source.get("prompt_version") or PROMPT_VERSION
        )
        final_rows.append(record)

    out_path = run_dir / "labels_final.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for record in final_rows:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()

    def agreement_with_human(rows: list[tuple[str, str]]) -> str:
        value = simple_agreement(rows)
        if value is None:
            return "null"
        matches = sum(1 for left, right in rows if left == right)
        return f"{value:.6f} ({matches}/{len(rows)})"

    print(f"Reviewed rows: {len(reviewed)}")
    print(f"Judge 1 agreement with human: {agreement_with_human(judge1_rows)}")
    print(f"Judge 2 agreement with human: {agreement_with_human(judge2_rows)}")
    print(
        f"Agreement checks corrected by human: {corrected} of {len(controls)}"
    )
    print(f"Wrote {len(final_rows)} rows to {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Two-judge adjudication (build review sheet, merge labels)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build", help="write the blind review sheet and its key"
    )
    build_parser.add_argument("--run-id", required=True, help="Run identifier")
    build_parser.add_argument(
        "--queries", default=str(DEFAULT_QUERIES), help="queries.csv path"
    )
    build_parser.add_argument(
        "--stories", default=str(DEFAULT_STORIES), help="stories.csv path"
    )
    build_parser.set_defaults(func=build)

    merge_parser = subparsers.add_parser(
        "merge", help="merge human labels into labels_final.jsonl"
    )
    merge_parser.add_argument("--run-id", required=True, help="Run identifier")
    merge_parser.set_defaults(func=merge)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
