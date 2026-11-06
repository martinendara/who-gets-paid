"""Offline tests for the volatility repeat selection (D65)."""

import csv
from pathlib import Path

from src import sample_repeat

FIXTURE = Path("data/fixtures/sample/queries_116.csv")


def _ids(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row["query_id"] for row in csv.DictReader(handle)]


def test_selection_is_deterministic(tmp_path):
    out_a = tmp_path / "a.csv"
    out_b = tmp_path / "b.csv"
    assert sample_repeat.main(["--queries", str(FIXTURE), "--out", str(out_a)]) == 0
    assert sample_repeat.main(["--queries", str(FIXTURE), "--out", str(out_b)]) == 0

    ids_a = _ids(out_a)
    ids_b = _ids(out_b)
    assert ids_a == ids_b
    assert len(ids_a) == 29
    assert ids_a == sorted(ids_a)

    with FIXTURE.open("r", encoding="utf-8", newline="") as handle:
        all_ids = {row["query_id"] for row in csv.DictReader(handle)}
    assert set(ids_a) <= all_ids


def test_wrong_row_count_exits_four(tmp_path):
    small = tmp_path / "small.csv"
    with small.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["query_id", "story_id", "lang", "type", "text"])
        writer.writerow(["S01-es-d", "S01", "es", "direct", "Q1"])
        writer.writerow(["S01-es-o", "S01", "es", "open", "Q2"])
        writer.writerow(["S01-en-d", "S01", "en", "direct", "Q3"])
    assert sample_repeat.main(
        ["--queries", str(small), "--out", str(tmp_path / "out.csv")]
    ) == sample_repeat.EXIT_BAD_INPUT
