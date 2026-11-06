"""Offline tests for robots.txt interpretation (D56). Fixtures use example.com."""

from pathlib import Path

from src import access_check
from src.access_check import (
    AGENTS,
    ENGINE_COLUMNS,
    ENGINES,
    assess_agents,
    robots_policy,
    robots_status_name,
)

FIXTURES = Path("data/fixtures/robots")
URL_A = "https://example.com/news/story-a"
URL_PRIVATE = "https://example.com/private/story"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _all_allowed(allows: dict[str, bool]) -> bool:
    return all(allows.values())


def _all_blocked(allows: dict[str, bool]) -> bool:
    return not any(allows.values())


def test_2xx_disallows_claude_user_not_googlebot():
    policy = robots_policy(200, _text("ok_claude_user.txt"))
    allows, engines = assess_agents(policy, URL_A)
    assert allows["googlebot"] is True
    assert allows["google_extended"] is True
    assert allows["claude_user"] is False
    assert engines["gemini"] == "open"
    assert engines["gemini_with_extended"] == "open"
    assert engines["claude"] == "closed"
    assert engines["openai"] == "open"
    assert engines["perplexity"] == "open"


def test_404_allows_everything():
    policy = robots_policy(404, _text("not_found.txt"))
    allows, engines = assess_agents(policy, URL_A)
    assert _all_allowed(allows)
    assert set(engines.values()) == {"open"}
    assert robots_status_name(404) == "unavailable"


def test_403_allows_everything():
    policy = robots_policy(403, _text("forbidden.txt"))
    allows, engines = assess_agents(policy, URL_A)
    assert _all_allowed(allows)
    assert set(engines.values()) == {"open"}
    assert robots_status_name(403) == "unavailable"


def test_503_blocks_everything():
    policy = robots_policy(503, _text("server_error.txt"))
    allows, engines = assess_agents(policy, URL_A)
    assert _all_blocked(allows)
    assert set(engines.values()) == {"closed"}
    assert robots_status_name(503) == "unreachable"


def test_network_failure_blocks_everything():
    policy = robots_policy(None, "")
    allows, engines = assess_agents(policy, URL_A)
    assert _all_blocked(allows)
    assert robots_status_name(None) == "unreachable"


def test_path_rule_affects_one_url_and_not_another():
    policy = robots_policy(200, _text("path_specific.txt"))
    public_allows, public_engines = assess_agents(policy, URL_A)
    private_allows, private_engines = assess_agents(policy, URL_PRIVATE)
    assert public_allows["googlebot"] is True
    assert public_allows["claude_user"] is True
    assert private_allows["googlebot"] is False
    assert set(public_engines.values()) == {"open"}
    assert set(private_engines.values()) == {"closed"}


def test_every_agent_belongs_to_one_engine():
    owners = [engine for _slug, _agent, engine in AGENTS]
    assert set(owners) == set(ENGINES)
    assert len({slug for slug, _agent, _engine in AGENTS}) == 8


def _write_inputs(tmp_path: Path, url: str) -> tuple[Path, Path]:
    import csv

    outlets = tmp_path / "outlets.csv"
    stories = tmp_path / "stories.csv"
    with outlets.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["outlet_id", "name", "country", "hosts", "rank_source"])
        writer.writerow(["T-01", "Outlet", "AR", "example.com", "fixture"])
    with stories.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["story_id", "outlet_id", "url"])
        writer.writerow(["S01", "T-01", url])
    return outlets, stories


def _read_output(out_dir: Path, run_id: str) -> list[dict]:
    import csv

    with (out_dir / f"access_{run_id}.csv").open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        return list(csv.DictReader(handle))


def test_robots_requested_from_exact_url_host(monkeypatch, tmp_path):
    outlets, stories = _write_inputs(
        tmp_path, "https://www.example.com/news/story"
    )
    calls: list[str] = []

    def fake_fetch(url, *, retries=0, timeout=20):
        calls.append(url)
        return 200, url, b"User-agent: *\nAllow: /\n", None

    monkeypatch.setattr(access_check, "fetch", fake_fetch)
    out_dir = tmp_path / "out"
    assert access_check.main(
        [
            "--run-id",
            "t1",
            "--outlets",
            str(outlets),
            "--stories",
            str(stories),
            "--out-dir",
            str(out_dir),
        ]
    ) == 0

    assert calls == [
        "https://www.example.com/robots.txt",
        "https://www.example.com/news/story",
    ]
    robots_dir = out_dir / "access" / "t1"
    assert (robots_dir / "www.example.com_robots.txt").exists()
    assert not (robots_dir / "example.com_robots.txt").exists()
    rows = _read_output(out_dir, "t1")
    assert rows[0]["robots_status"] == "ok"
    assert rows[0]["robots_error"] == ""


def test_unreachable_leaves_measured_fields_empty(monkeypatch, tmp_path):
    outlets, stories = _write_inputs(
        tmp_path, "https://www.example.com/news/story"
    )

    def fake_fetch(url, *, retries=0, timeout=20):
        return None, None, None, "timed out"

    monkeypatch.setattr(access_check, "fetch", fake_fetch)
    out_dir = tmp_path / "out"
    assert access_check.main(
        [
            "--run-id",
            "t2",
            "--outlets",
            str(outlets),
            "--stories",
            str(stories),
            "--out-dir",
            str(out_dir),
        ]
    ) == 0

    row = _read_output(out_dir, "t2")[0]
    assert row["robots_status"] == "unreachable"
    assert row["robots_http_status"] == ""
    assert row["robots_sha256"] == ""
    assert row["robots_error"] == "timed out"
    assert all(row[f"allow_{slug}"] == "false" for slug, _a, _e in AGENTS)
    assert all(row[f"engine_{engine}"] == "closed" for engine in ENGINE_COLUMNS)
    assert row["page_http_status"] == ""
    assert row["page_final_url"] == ""
    assert row["page_error"] == "timed out"


def test_google_extended_does_not_close_gemini():
    policy = robots_policy(200, _text("google_extended_disallow.txt"))
    allows, engines = assess_agents(policy, URL_A)
    assert allows["googlebot"] is True
    assert allows["google_extended"] is False
    assert engines["gemini"] == "open"
    assert engines["gemini_with_extended"] == "closed"
