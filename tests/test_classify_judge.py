"""Offline tests for the two classifier judges (D75). No network."""

import json

from src import classify, prices


def test_deepseek_judge_config_unchanged():
    judge = classify.JUDGES["deepseek"]
    assert judge.name == "deepseek"
    assert judge.model == "deepseek-flash"
    assert judge.base_url == "https://api.deepseek.com"
    assert judge.api_key_env == "DEEPSEEK_API_KEY"
    assert judge.extra_body == {"thinking": {"type": "disabled"}}
    assert judge.default_output == "labels_raw.jsonl"
    assert judge.local_pricing is True


def test_nemotron_judge_config():
    judge = classify.JUDGES["nemotron"]
    assert judge.name == "nemotron"
    assert judge.model == "nvidia/nemotron-3-ultra-550b-a55b:free"
    assert judge.base_url == "https://openrouter.ai/api/v1"
    assert judge.api_key_env == "OPENROUTER_API_KEY"
    assert judge.extra_body == {
        "reasoning": {"enabled": False},
        "usage": {"include": True},
    }
    assert judge.default_output == "labels_raw_judge2.jsonl"
    assert judge.local_pricing is False


def test_skip_uses_selected_judge_model():
    deepseek = classify.JUDGES["deepseek"]
    nemotron = classify.JUDGES["nemotron"]
    prompt = classify.PROMPT_VERSION

    record = {"classifier_model": nemotron.model, "prompt_version": prompt}
    assert classify._already_labeled(record, nemotron) is True
    assert classify._already_labeled(record, deepseek) is False

    assert classify._already_labeled(None, nemotron) is False
    assert classify._already_labeled(
        {"classifier_model": nemotron.model, "prompt_version": "other"}, nemotron
    ) is False
    assert classify._already_labeled(
        {"classifier_model": "legacy", "prompt_version": prompt}, nemotron
    ) is False


def test_judge_cost_nemotron_prefers_response_cost():
    judge = classify.JUDGES["nemotron"]
    assert classify._judge_cost(judge, {"cost_usd": 0.0}) == 0.0
    assert classify._judge_cost(judge, {"cost_usd": 0.001}) == 0.001
    assert classify._judge_cost(judge, {"input_tokens": 1}) == 0.0
    assert classify._judge_cost(judge, None) == 0.0


def test_judge_cost_deepseek_uses_local_pricing():
    judge = classify.JUDGES["deepseek"]
    usage = {"input_tokens": 1000, "output_tokens": 2}
    assert classify._judge_cost(judge, usage) == prices.classifier_cost(usage)


def test_main_skips_pairs_already_labeled_by_the_selected_judge(
    tmp_path, monkeypatch
):
    run_id = "R"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "answers.jsonl").write_text(
        json.dumps(
            {
                "query_id": "q1",
                "engine": "gemini",
                "answer_text": "Paris.",
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "labels_raw_judge2.jsonl").write_text(
        json.dumps(
            {
                "query_id": "q1",
                "engine": "gemini",
                "classifier_model": classify.JUDGES["nemotron"].model,
                "prompt_version": classify.PROMPT_VERSION,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    queries = tmp_path / "queries.csv"
    queries.write_text("query_id,story_id\nq1,S1\n", encoding="utf-8")
    stories = tmp_path / "stories.csv"
    stories.write_text("story_id,fact_key\nS1,The Eiffel Tower is in Paris.\n", encoding="utf-8")

    monkeypatch.setattr(classify, "DEFAULT_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")

    code = classify.main(
        [
            "--run-id",
            run_id,
            "--judge",
            "nemotron",
            "--queries",
            str(queries),
            "--stories",
            str(stories),
        ]
    )
    assert code == 0
