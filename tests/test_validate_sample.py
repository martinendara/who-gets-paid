"""Offline tests for the sample validator (D57). Fixtures are synthetic."""

from datetime import date
from pathlib import Path

from src import validate_sample

FIXTURES = Path("data/fixtures/sample")
STORIES_VALID = FIXTURES / "stories_valid.csv"
STORIES_INVALID = FIXTURES / "stories_invalid.csv"
OUTLETS = FIXTURES / "outlets.csv"
EXCEPTIONS = FIXTURES / "exceptions.csv"


def _validate(stories_path: Path):
    return validate_sample.validate(stories_path, OUTLETS, EXCEPTIONS)


def test_valid_sample_passes():
    problems, warnings = _validate(STORIES_VALID)
    assert problems == []
    assert any("saga column absent" in warning for warning in warnings)


def test_parse_accepts_date_only_and_timestamp():
    assert validate_sample.parse_published_at("2025-11-16") == date(2025, 11, 16)
    assert (
        validate_sample.parse_published_at("2026-08-16T23:00:00-03:00")
        == date(2026, 8, 16)
    )
    assert validate_sample.parse_published_at("PENDING") is None
    assert validate_sample.parse_published_at("not-a-date") is None


def test_invalid_sample_lists_every_problem():
    problems, warnings = _validate(STORIES_INVALID)
    text = "\n".join(problems)
    assert "duplicate story_id 'S01'" in text
    assert "outlet_id 'T-99' is not in" in text
    assert "outside 2025-11-16..2026-08-16" in text
    assert "origin_criterion 'hearsay'" in text
    assert "fact_key is PENDING" in text
    assert "english_replica_url is PENDING" in text
    assert "outlet_id 'T-02' has 1 story; fewer than 3" in text
    assert "outlet_id 'T-03' has 0 stories; fewer than 3" in text
    assert "saga 'A'; more than 2" in text
    assert warnings == []


def test_main_exit_codes(capsys):
    assert validate_sample.main(
        ["--stories", str(STORIES_VALID), "--outlets", str(OUTLETS),
         "--exceptions", str(EXCEPTIONS)]
    ) == 0
    assert validate_sample.main(
        ["--stories", str(STORIES_INVALID), "--outlets", str(OUTLETS),
         "--exceptions", str(EXCEPTIONS)]
    ) == validate_sample.EXIT_PROBLEMS
    captured = capsys.readouterr()
    assert "Sample validation failed" in captured.out
