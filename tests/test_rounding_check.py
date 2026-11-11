"""Offline tests for the rounding re-check of human ``wrong`` labels (D78)."""

import csv
import json
from pathlib import Path

import pytest

from src import classify, rounding_check

FIELDS = ["review_id", "fact_key", "query_text", "answer_text", "human_fact_present"]


def _write_review(
    path: Path, rows: list[dict], terminator: str = "\n"
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS, lineterminator=terminator
        )
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _row(review_id: str, answer_text: str, human: str) -> dict:
    return {
        "review_id": review_id,
        "fact_key": "The population was 3.796 billion.",
        "query_text": "question",
        "answer_text": answer_text,
        "human_fact_present": human,
    }


def test_select_wrong_only_reads_wrong_rows():
    rows = [
        _row("R-1", "a", "yes"),
        _row("R-2", "b", "wrong"),
        _row("R-3", "c", "no"),
        _row("R-4", "d", "wrong"),
    ]
    selected = rounding_check.select_wrong(rows)
    assert [row["review_id"] for row in selected] == ["R-2", "R-4"]


def test_rewrite_turns_yes_into_yes_and_keeps_the_rest(tmp_path):
    path = tmp_path / "review_R.csv"
    rows = [
        _row("R-1", "a", "wrong"),
        _row("R-2", "b", "wrong"),
        _row("R-3", "c", "wrong"),
        _row("R-4", "d", "no"),
    ]
    _write_review(path, rows)

    fieldnames, read_rows = rounding_check._read_review(path)
    rounding_check.rewrite(
        path,
        fieldnames,
        read_rows,
        {"R-1": "yes", "R-2": "no", "R-3": None},
    )

    result = {row["review_id"]: row for row in _read_csv(path)}
    assert result["R-1"]["human_fact_present"] == "yes"
    assert result["R-2"]["human_fact_present"] == "wrong"
    assert result["R-3"]["human_fact_present"] == "wrong"
    assert result["R-4"]["human_fact_present"] == "no"
    assert list(result["R-1"]) == FIELDS
    assert result["R-1"]["answer_text"] == "a"
    # The human sheet uses LF; the rewrite must not switch it to CRLF.
    assert b"\r\n" not in path.read_bytes()


def test_rewrite_preserves_crlf_input(tmp_path):
    path = tmp_path / "review_R.csv"
    _write_review(path, [_row("R-1", "a", "wrong")], terminator="\r\n")
    fieldnames, rows = rounding_check._read_review(path)
    rounding_check.rewrite(path, fieldnames, rows, {"R-1": "no"})
    data = path.read_bytes()
    assert data.count(b"\r\n") == 2
    assert data.count(b"\n") == data.count(b"\r\n")


def test_main_end_to_end_without_network(tmp_path, monkeypatch):
    review_path = tmp_path / "review" / "review_R.csv"
    rows = [
        _row("R-1", "ANSWER-ALPHA", "wrong"),
        _row("R-2", "ANSWER-BETA", "wrong"),
        _row("R-3", "ANSWER-GAMMA", "wrong"),
        _row("R-4", "ANSWER-DELTA", "no"),
    ]
    _write_review(review_path, rows)
    original = review_path.read_bytes()

    calls: list[str] = []

    def fake_call(client, prompt, judge):
        calls.append(prompt)
        if "ANSWER-ALPHA" in prompt:
            content = "yes"
        elif "ANSWER-BETA" in prompt:
            content = "maybe"
        else:
            content = "no"
        return (
            {
                "choices": [{"message": {"content": content}}],
                "model": "deepseek-flash",
                "system_fingerprint": "fp-test",
                "usage": {"prompt_tokens": 10, "completion_tokens": 1},
            },
            None,
        )

    monkeypatch.setattr(rounding_check, "DEFAULT_REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(rounding_check, "DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(classify, "_call", fake_call)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")

    assert rounding_check.main(["--run-id", "R"]) == 0

    result = {row["review_id"]: row for row in _read_csv(review_path)}
    assert result["R-1"]["human_fact_present"] == "yes"
    assert result["R-2"]["human_fact_present"] == "wrong"
    assert result["R-3"]["human_fact_present"] == "wrong"
    assert result["R-4"]["human_fact_present"] == "no"

    backup = tmp_path / "review" / "review_R.before_rounding.csv"
    assert backup.read_bytes() == original
    assert b"\r\n" not in review_path.read_bytes()

    out_path = tmp_path / "runs" / "R" / "rounding_check.jsonl"
    records = [
        json.loads(line)
        for line in out_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["review_id"] for record in records] == ["R-1", "R-2", "R-3"]
    assert [record["check_output"] for record in records] == ["yes", None, "no"]
    assert all(record["human_original"] == "wrong" for record in records)
    assert all(
        record["prompt_version"] == "rounding_check_v1" for record in records
    )
    assert all(record["model"] == "deepseek-flash" for record in records)
    assert records[1]["raw_output"] == "maybe"
    assert records[1]["usage"] == {"input_tokens": 20, "output_tokens": 2}
    assert records[0]["usage"] == {"input_tokens": 10, "output_tokens": 1}
    assert all(record["cost_usd"] is not None for record in records)
    assert all(record["checked_at"].endswith("Z") for record in records)
    # The invalid answer is retried once, then recorded as null.
    assert len(calls) == 4


def test_main_stops_when_backup_already_exists(tmp_path, monkeypatch):
    review_path = tmp_path / "review" / "review_R.csv"
    _write_review(review_path, [_row("R-1", "a", "wrong")])
    backup = tmp_path / "review" / "review_R.before_rounding.csv"
    backup.write_bytes(b"untouched")
    original = review_path.read_bytes()

    def exploding_call(client, prompt, judge):
        raise AssertionError("no API call is allowed when the check already ran")

    monkeypatch.setattr(rounding_check, "DEFAULT_REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(rounding_check, "DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(classify, "_call", exploding_call)

    assert rounding_check.main(["--run-id", "R"]) == 4
    assert review_path.read_bytes() == original
    assert backup.read_bytes() == b"untouched"
    assert not (tmp_path / "runs" / "R" / "rounding_check.jsonl").exists()


def test_main_without_wrong_rows_does_nothing(tmp_path, monkeypatch):
    review_path = tmp_path / "review" / "review_R.csv"
    _write_review(
        review_path,
        [_row("R-1", "a", "yes"), _row("R-2", "b", "no")],
    )

    monkeypatch.setattr(rounding_check, "DEFAULT_REVIEW_DIR", tmp_path / "review")
    monkeypatch.setattr(rounding_check, "DEFAULT_RUNS_DIR", tmp_path / "runs")

    assert rounding_check.main(["--run-id", "R"]) == 0
    assert not (tmp_path / "review" / "review_R.before_rounding.csv").exists()
    assert not (tmp_path / "runs" / "R" / "rounding_check.jsonl").exists()


def test_read_review_rejects_duplicate_review_ids(tmp_path):
    path = tmp_path / "review_R.csv"
    _write_review(path, [_row("R-1", "a", "wrong"), _row("R-1", "b", "wrong")])
    with pytest.raises(SystemExit):
        rounding_check._read_review(path)
