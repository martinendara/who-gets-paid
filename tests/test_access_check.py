"""Offline tests for robots.txt interpretation (D56). Fixtures use example.com."""

from pathlib import Path

from src.access_check import (
    AGENTS,
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
