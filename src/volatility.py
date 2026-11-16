"""Code agreement between the main run and the repeat run (D80, D95).

Reads two ``labels.csv``-shaped files and compares the (query_id, engine)
pairs of the repeat run against the main run. A pair present in the repeat
but absent from the main run stops the run with exit code 4: pairs are never
dropped silently.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_QUERIES = Path("data/private/queries.csv")
REQUIRED_LABELS = {"query_id", "engine", "fact_present", "code"}
QUESTION_TYPES = ("direct", "open")


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


def _index(path: Path) -> dict[tuple[str, str], dict]:
    rows: dict[tuple[str, str], dict] = {}
    for row in _read_csv(path, REQUIRED_LABELS):
        query_id = (row.get("query_id") or "").strip()
        engine = (row.get("engine") or "").strip()
        if not query_id or not engine:
            raise SystemExit(f"{path}: empty query_id or engine")
        pair = (query_id, engine)
        if pair in rows:
            raise SystemExit(
                f"{path}: duplicate pair {query_id!r}/{engine!r}"
            )
        rows[pair] = row
    return rows


def _code(row: dict) -> str:
    return (row.get("code") or "").strip()


def _fact(row: dict) -> str:
    return (row.get("fact_present") or "").strip()


def _stats(pairs: list[tuple[dict, dict]]) -> dict:
    n = len(pairs)
    same_code = sum(1 for main, repeat in pairs if _code(main) == _code(repeat))
    transitions = Counter(
        f"{_code(main)}->{_code(repeat)}"
        for main, repeat in pairs
        if _code(main) != _code(repeat)
    )
    return {
        "n": n,
        "same_code": same_code,
        "agreement": None if n == 0 else same_code / n,
        "same_fact_present": sum(
            1 for main, repeat in pairs if _fact(main) == _fact(repeat)
        ),
        "a_main": sum(1 for main, _ in pairs if _code(main) == "A"),
        "a_repeat": sum(1 for _, repeat in pairs if _code(repeat) == "A"),
        "a_both": sum(
            1
            for main, repeat in pairs
            if _code(main) == "A" and _code(repeat) == "A"
        ),
        "transitions": {
            transition: count
            for transition, count in sorted(transitions.items())
            if count > 0
        },
    }


def _fmt(value) -> str:
    return "null" if value is None else f"{value:.6f}"


def build(
    main_labels: Path, repeat_labels: Path, queries: Path
) -> dict:
    main = _index(main_labels)
    repeat = _index(repeat_labels)

    missing = sorted(pair for pair in repeat if pair not in main)
    if missing:
        print(
            "Pairs in the repeat run with no row in the main run:",
            file=sys.stderr,
        )
        for query_id, engine in missing:
            print(f"  {query_id}/{engine}", file=sys.stderr)
        raise SystemExit(4)

    query_type: dict[str, str] = {}
    for row in _read_csv(queries, {"query_id", "type"}):
        query_id = (row.get("query_id") or "").strip()
        question_type = (row.get("type") or "").strip()
        if not query_id:
            raise SystemExit(f"{queries}: empty query_id")
        if query_id in query_type:
            raise SystemExit(f"{queries}: duplicate query_id {query_id!r}")
        query_type[query_id] = question_type

    pairs = [(main[pair], repeat[pair]) for pair in sorted(repeat)]
    for main_row, _ in pairs:
        query_id = (main_row.get("query_id") or "").strip()
        if query_id not in query_type:
            raise SystemExit(
                f"query_id {query_id!r} is not in {queries}"
            )

    engines = sorted({engine for _, engine in repeat})
    by_engine = {
        engine: _stats(
            [(m, r) for m, r in pairs if (m.get("engine") or "").strip() == engine]
        )
        for engine in engines
    }
    by_type = {
        question_type: _stats(
            [
                (m, r)
                for m, r in pairs
                if query_type[(m.get("query_id") or "").strip()]
                == question_type
            ]
        )
        for question_type in QUESTION_TYPES
    }

    return {
        "total": _stats(pairs),
        "by_engine": by_engine,
        "by_type": by_type,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Code agreement between the main and repeat runs (D95)."
    )
    parser.add_argument(
        "--main-labels", required=True, help="main labels.csv path"
    )
    parser.add_argument(
        "--repeat-labels", required=True, help="repeat labels.csv path"
    )
    parser.add_argument(
        "--queries", default=str(DEFAULT_QUERIES), help="queries.csv path"
    )
    parser.add_argument("--out", required=True, help="JSON output path")
    args = parser.parse_args(argv)

    payload = build(
        Path(args.main_labels),
        Path(args.repeat_labels),
        Path(args.queries),
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")

    print(f"{'engine':<12}{'n':>4}{'same_code':>12}{'agreement':>14}")
    for engine in sorted(payload["by_engine"]):
        stats = payload["by_engine"][engine]
        print(
            f"{engine:<12}{stats['n']:>4}{stats['same_code']:>12}"
            f"{_fmt(stats['agreement']):>14}"
        )
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
