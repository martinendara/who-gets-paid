"""Offline tests for code agreement between runs (D95)."""

import csv
from pathlib import Path

import pytest

from src import volatility

FIELDS = ["query_id", "engine", "fact_present", "code"]


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _write_queries(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["query_id", "type"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def _row(query_id: str, engine: str, code: str, fact: str = "yes") -> dict:
    return {
        "query_id": query_id,
        "engine": engine,
        "fact_present": fact,
        "code": code,
    }


def test_total_agreement(tmp_path):
    main = [
        _row("Q1", "gemini", "A"),
        _row("Q1", "claude", "B"),
        _row("Q2", "gemini", "E", fact="no"),
    ]
    repeat = [dict(row) for row in main]
    queries = [
        {"query_id": "Q1", "type": "direct"},
        {"query_id": "Q2", "type": "open"},
    ]

    payload = volatility.build(
        _write(tmp_path / "main.csv", main),
        _write(tmp_path / "repeat.csv", repeat),
        _write_queries(tmp_path / "queries.csv", queries),
    )

    total = payload["total"]
    assert total["n"] == 3
    assert total["same_code"] == 3
    assert total["agreement"] == 1.0
    assert total["same_fact_present"] == 3
    assert total["transitions"] == {}
    assert payload["by_engine"]["gemini"]["agreement"] == 1.0


def test_partial_agreement_with_transitions(tmp_path):
    main = [
        _row("Q1", "gemini", "A"),
        _row("Q2", "gemini", "B"),
        _row("Q3", "gemini", "A"),
    ]
    repeat = [
        _row("Q1", "gemini", "A"),
        _row("Q2", "gemini", "C"),
        _row("Q3", "gemini", "B"),
    ]
    queries = [
        {"query_id": "Q1", "type": "direct"},
        {"query_id": "Q2", "type": "direct"},
        {"query_id": "Q3", "type": "open"},
    ]

    payload = volatility.build(
        _write(tmp_path / "main.csv", main),
        _write(tmp_path / "repeat.csv", repeat),
        _write_queries(tmp_path / "queries.csv", queries),
    )

    total = payload["total"]
    assert total["n"] == 3
    assert total["same_code"] == 1
    assert total["agreement"] == pytest.approx(1 / 3)
    assert total["a_main"] == 2
    assert total["a_repeat"] == 1
    assert total["a_both"] == 1
    assert total["transitions"] == {"A->B": 1, "B->C": 1}
    assert payload["by_type"]["direct"]["n"] == 2
    assert payload["by_type"]["direct"]["same_code"] == 1
    assert payload["by_type"]["open"]["transitions"] == {"A->B": 1}


def test_missing_pair_exits_with_code_4(tmp_path):
    main = [_row("Q1", "gemini", "A")]
    repeat = [_row("Q1", "gemini", "A"), _row("Q2", "claude", "B")]
    queries = [
        {"query_id": "Q1", "type": "direct"},
        {"query_id": "Q2", "type": "direct"},
    ]

    with pytest.raises(SystemExit) as excinfo:
        volatility.build(
            _write(tmp_path / "main.csv", main),
            _write(tmp_path / "repeat.csv", repeat),
            _write_queries(tmp_path / "queries.csv", queries),
        )
    assert excinfo.value.code == 4


def test_empty_repeat_agreement_is_null(tmp_path):
    queries = _write_queries(tmp_path / "queries.csv", [])

    payload = volatility.build(
        _write(tmp_path / "main.csv", [_row("Q1", "gemini", "A")]),
        _write(tmp_path / "repeat.csv", []),
        queries,
    )

    assert payload["total"]["n"] == 0
    assert payload["total"]["same_code"] == 0
    assert payload["total"]["agreement"] is None
    assert payload["by_type"]["direct"]["agreement"] is None
