"""Select the volatility repeat sample (D65).

Chooses 29 of the 116 sample queries with ``random.Random(20261106).sample``
over the list sorted by ``query_id`` and writes them, sorted by ``query_id``
and with the same header, to ``data/private/queries_repeat.csv``.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from collections import Counter
from pathlib import Path

DEFAULT_QUERIES = Path("data/private/queries.csv")
DEFAULT_OUT = Path("data/private/queries_repeat.csv")
SEED = 20261106
SAMPLE_SIZE = 29
EXPECTED_ROWS = 116
EXIT_BAD_INPUT = 4


def select(rows: list[dict]) -> list[dict]:
    """Return the deterministic repeat sample, sorted by ``query_id``."""
    ordered = sorted(rows, key=lambda row: row["query_id"])
    chosen = random.Random(SEED).sample(ordered, SAMPLE_SIZE)
    return sorted(chosen, key=lambda row: row["query_id"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Select 29 queries for the volatility repeat (D65)."
    )
    parser.add_argument("--queries", default=str(DEFAULT_QUERIES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    input_path = Path(args.queries)
    if not input_path.exists():
        print(f"input file not found: {input_path}", file=sys.stderr)
        return EXIT_BAD_INPUT

    with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            print(f"{input_path}: empty CSV or missing header", file=sys.stderr)
            return EXIT_BAD_INPUT
        fieldnames = reader.fieldnames
        rows = list(reader)

    if len(rows) != EXPECTED_ROWS:
        print(
            f"{input_path}: expected {EXPECTED_ROWS} queries, got {len(rows)}",
            file=sys.stderr,
        )
        return EXIT_BAD_INPUT

    selected = select(rows)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(selected)

    langs = Counter(row["lang"] for row in selected)
    types = Counter(row["type"] for row in selected)
    print(f"Selected {len(selected)} of {len(rows)} queries (seed {SEED})")
    print("By lang: " + ", ".join(f"{k}={v}" for k, v in sorted(langs.items())))
    print("By type: " + ", ".join(f"{k}={v}" for k, v in sorted(types.items())))
    print(f"Output: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
