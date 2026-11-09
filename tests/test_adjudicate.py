"""Offline tests for two-judge adjudication (D73)."""

import csv
import json
from pathlib import Path

from src import adjudicate


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_kappa_perfect_and_single_category():
    assert adjudicate.cohen_kappa(
        [("yes", "yes"), ("no", "no"), ("wrong", "wrong")]
    ) == 1.0
    assert adjudicate.cohen_kappa([("yes", "yes"), ("yes", "yes")]) is None
    assert adjudicate.cohen_kappa([]) is None


def test_kappa_known_example():
    rows = [("yes", "yes"), ("yes", "no"), ("no", "no"), ("no", "no")]
    # po = 3/4; pe = 1/2*1/4 + 1/2*3/4 = 1/2; kappa = (3/4-1/2)/(1/2) = 1/2.
    assert adjudicate.cohen_kappa(rows) == 0.5
    assert adjudicate.simple_agreement(rows) == 0.75


def _fixture(tmp_path: Path, monkeypatch, agreement_n: int, disagreement_n: int):
    run_dir = tmp_path / "runs" / "R"
    queries: list[dict] = []
    answers: list[dict] = []
    judge1: list[dict] = []
    judge2: list[dict] = []
    total = agreement_n + disagreement_n
    for index in range(1, total + 1):
        query_id = f"q{index:02d}"
        pair = (query_id, "gemini")
        queries.append(
            {"query_id": query_id, "story_id": f"S{index}", "text": f"query {index}"}
        )
        answers.append(
            {
                "query_id": query_id,
                "engine": "gemini",
                "answer_text": f"answer {index}",
                "error": None,
            }
        )
        if index <= agreement_n:
            label1 = label2 = "yes" if index % 2 else "no"
        else:
            label1, label2 = "yes", "no"
        for labels, label in ((judge1, label1), (judge2, label2)):
            labels.append(
                {
                    "query_id": query_id,
                    "engine": "gemini",
                    "fact_present": label,
                    "classifier_model": "m",
                    "prompt_version": adjudicate.PROMPT_VERSION,
                }
            )

    _write_jsonl(run_dir / "answers.jsonl", answers)
    _write_jsonl(run_dir / "labels_raw.jsonl", judge1)
    _write_jsonl(run_dir / "labels_raw_judge2.jsonl", judge2)

    queries_path = tmp_path / "queries.csv"
    with queries_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["query_id", "story_id", "text"]
        )
        writer.writeheader()
        writer.writerows(queries)

    stories_path = tmp_path / "stories.csv"
    with stories_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["story_id", "fact_key"])
        writer.writeheader()
        for index in range(1, total + 1):
            writer.writerow(
                {"story_id": f"S{index}", "fact_key": f"fact {index}"}
            )

    monkeypatch.setattr(adjudicate, "DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(adjudicate, "DEFAULT_REVIEW_DIR", tmp_path / "review")
    return queries_path, stories_path


def test_build_selection_and_seed(tmp_path, monkeypatch):
    queries_path, stories_path = _fixture(
        tmp_path, monkeypatch, agreement_n=40, disagreement_n=3
    )
    args = [
        "build",
        "--run-id",
        "R",
        "--queries",
        str(queries_path),
        "--stories",
        str(stories_path),
    ]
    assert adjudicate.main(args) == 0
    review_path = tmp_path / "review" / "review_R.csv"
    key_path = tmp_path / "review" / "review_key_R.csv"
    first_review = review_path.read_bytes()
    first_key = key_path.read_bytes()

    review = _read_csv(review_path)
    key = _read_csv(key_path)
    assert [row["review_id"] for row in review] == [
        f"R-{i:04d}" for i in range(1, 34)
    ]
    assert list(review[0]) == adjudicate.REVIEW_FIELDS
    assert "engine" not in review[0] and "judge1" not in review[0]
    sets = [row["set"] for row in key]
    assert sets.count("disagreement") == 3
    assert sets.count("agreement_check") == 30

    assert adjudicate.main(args) == 0
    assert review_path.read_bytes() == first_review
    assert key_path.read_bytes() == first_key


def test_merge_uses_human_and_agreed_labels(tmp_path, monkeypatch):
    queries_path, stories_path = _fixture(
        tmp_path, monkeypatch, agreement_n=40, disagreement_n=3
    )
    args = [
        "build",
        "--run-id",
        "R",
        "--queries",
        str(queries_path),
        "--stories",
        str(stories_path),
    ]
    assert adjudicate.main(args) == 0

    review_path = tmp_path / "review" / "review_R.csv"
    key_path = tmp_path / "review" / "review_key_R.csv"
    review = _read_csv(review_path)
    key = {row["review_id"]: row for row in _read_csv(key_path)}

    assert adjudicate.main(["merge", "--run-id", "R"]) == 4

    corrected = 0
    for row in review:
        pair = key[row["review_id"]]
        if pair["set"] == "disagreement":
            row["human_fact_present"] = pair["judge1"]
        else:
            if corrected == 0 and pair["judge1"] == pair["judge2"]:
                row["human_fact_present"] = "wrong"
                corrected += 1
            else:
                row["human_fact_present"] = pair["judge1"]
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=adjudicate.REVIEW_FIELDS)
        writer.writeheader()
        writer.writerows(review)

    assert adjudicate.main(["merge", "--run-id", "R"]) == 0

    final_path = tmp_path / "runs" / "R" / "labels_final.jsonl"
    final = {
        (record["query_id"], record["engine"]): record
        for record in (
            json.loads(line)
            for line in final_path.read_text(encoding="utf-8").splitlines()
        )
    }
    assert len(final) == 43
    human = {
        (key[row["review_id"]]["query_id"], key[row["review_id"]]["engine"]):
        row["human_fact_present"]
        for row in review
    }
    for pair, record in final.items():
        assert record["classifier_model"] == "adjudicated"
        if pair in human:
            assert record["fact_present"] == human[pair]
        else:
            assert record["fact_present"] in {"yes", "no"}
