"""Offline tests for the metrics module and its story bootstrap (D74)."""

import pytest

from src import metrics


def _row(
    story="S1",
    engine="gemini",
    lang="es",
    question_type="direct",
    code="A",
    d_split="",
    retrieved="",
    cited="",
):
    return {
        "story_id": story,
        "engine": engine,
        "lang": lang,
        "type": question_type,
        "code": code,
        "d_split": d_split,
        "fact_present": "yes" if code in ("A", "B", "C", "D") else "no",
        "original_in_retrieved": retrieved,
        "original_url_cited": cited,
    }


def test_zero_denominators_are_null_never_zero():
    payload = metrics.summarize([], resamples=25)
    assert payload["N"] == 0
    for name in metrics.MAIN_RATES:
        assert payload[name]["value"] is None
        assert payload[name]["ci_low"] is None
        assert payload[name]["ci_high"] is None
        assert payload[name]["n_valid"] == 0

    only_e = metrics.summarize([_row(code="E")], resamples=25)
    # N > 0, so fact presence has a value; every P-based rate stays null.
    assert only_e["fact_presence"]["value"] == 0.0
    for name in ("original_attribution", "no_classified", "d_none"):
        assert only_e[name]["value"] is None
        assert only_e[name]["n_valid"] == 0


def test_counts_and_main_rates():
    rows = [
        _row("S1", lang="es", code="A"),
        _row("S1", lang="en", code="B"),
        _row("S2", lang="es", code="C"),
        _row("S2", lang="en", code="D", d_split="none"),
        _row("S3", lang="es", code="D", d_split="other"),
        _row("S3", lang="en", code="E"),
    ]
    payload = metrics.summarize(rows, resamples=50)
    assert (payload["N"], payload["A"], payload["B"], payload["C"]) == (6, 1, 1, 1)
    assert (payload["D"], payload["D_none"], payload["D_other"]) == (2, 1, 1)
    assert payload["E"] == 1
    assert payload["P"] == 5
    assert payload["fact_presence"]["value"] == pytest.approx(5 / 6)
    assert payload["original_attribution"]["value"] == pytest.approx(1 / 5)
    assert payload["es_displacement"]["value"] == pytest.approx(1 / 5)
    assert payload["en_displacement"]["value"] == pytest.approx(1 / 5)
    assert payload["no_classified"]["value"] == pytest.approx(2 / 5)
    assert payload["d_none"]["value"] == pytest.approx(1 / 5)
    assert payload["d_other"]["value"] == pytest.approx(1 / 5)


def test_rates_stay_in_range_and_gap_uses_language_denominators():
    counts = metrics._counts(
        [
            _row("S1", lang="es", code="A"),
            _row("S2", lang="en", code="B"),
            _row("S3", lang="en", code="A"),
        ]
    )
    assert metrics._gap(counts) == pytest.approx(1.0 - 0.5)

    only_spanish = metrics._counts([_row("S1", lang="es", code="A")])
    assert metrics._gap(only_spanish) is None

    for name in metrics.MAIN_RATES:
        value = metrics.METRIC_VALUES[name](counts)
        assert value is not None and 0.0 <= value <= 1.0


def test_out_of_range_values_raise():
    with pytest.raises(ValueError):
        metrics._rate(1.5, "rate")
    with pytest.raises(ValueError):
        metrics._rate(-0.1, "rate")
    with pytest.raises(ValueError):
        metrics._ratio(3, 2, "rate")
    assert metrics._rate(-0.5, "gap", -1.0, 1.0) == -0.5
    with pytest.raises(ValueError):
        metrics._rate(1.5, "gap", -1.0, 1.0)


def test_bootstrap_is_deterministic_and_bounded():
    rows = []
    for index in range(6):
        story = f"S{index}"
        rows.append(_row(story, lang="es", code="A" if index % 2 else "B"))
        rows.append(_row(story, lang="en", code="C" if index % 3 else "E"))
        rows.append(
            _row(
                story,
                lang="en",
                code="D",
                d_split="none" if index % 2 else "other",
            )
        )
    cells = metrics._cells(rows)
    names = ("fact_presence", "original_attribution", "attribution_gap")
    first = metrics.bootstrap_all(cells, names, resamples=100)
    second = metrics.bootstrap_all(cells, names, resamples=100)
    assert first == second

    for name in ("fact_presence", "original_attribution"):
        interval = first[name]
        assert 0 <= interval["n_valid"] <= 100
        assert 0.0 <= interval["ci_low"] <= interval["ci_high"] <= 1.0
    gap = first["attribution_gap"]
    assert -1.0 <= gap["ci_low"] <= gap["ci_high"] <= 1.0


def test_bootstrap_discards_resamples_with_zero_denominator():
    cells = metrics._cells([_row("S1", code="E"), _row("S2", code="E")])
    intervals = metrics.bootstrap_all(
        cells, ("original_attribution",), resamples=100
    )
    assert intervals["original_attribution"]["n_valid"] == 0
    assert intervals["original_attribution"]["ci_low"] is None


def test_found_not_cited_and_story_cited_numerators():
    rows = [
        _row("S1", code="A", retrieved="true", cited="true"),
        _row("S1", code="B", retrieved="true", cited="false"),
        _row("S2", code="B", retrieved="false", cited="true"),
        _row("S2", code="E", retrieved="true", cited="true"),
        _row("S3", code="A", retrieved="true", cited="false"),
    ]
    counts = metrics._counts(rows)
    assert counts["p"] == 4
    # fact yes, retrieved true, code != A: only the S1 B row qualifies.
    assert counts["fnc"] == 1
    # story cited counts every cited URL, including the fact-absent row.
    assert counts["cited"] == 3
    assert metrics.METRIC_VALUES["found_not_cited"](counts) == pytest.approx(0.25)
    assert metrics.METRIC_VALUES["story_cited"](counts) == pytest.approx(0.75)


def test_percentile_edges():
    assert metrics._percentile([], 50) is None
    assert metrics._percentile([0.4], 2.5) == 0.4
    assert metrics._percentile([0.0, 1.0], 50) == 0.5
