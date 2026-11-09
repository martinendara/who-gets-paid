"""Blind classifier: reference fact + answer text -> yes/no/wrong."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from openai import AuthenticationError, OpenAI, RateLimitError

from src import prices

# DeepSeek docs verified 2026-11-04:
# https://api-docs.deepseek.com/api/create-chat-completion
# https://api-docs.deepseek.com/quick_start/pricing
MODEL_ID = "deepseek-flash"
BASE_URL = "https://api.deepseek.com"
PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "classifier_v1.md"
PROMPT_VERSION = PROMPT_PATH.stem
DEFAULT_QUERIES = Path("data/private/queries.csv")
DEFAULT_STORIES = Path("data/private/stories.csv")
DEFAULT_RUNS_DIR = Path("runs")
MAX_OUTPUT_TOKENS = 5
VALID_LABELS = {"yes", "no", "wrong"}

# OpenRouter model catalog checked 2026-11-09: this is the only
# nvidia/nemotron-3-ultra*:free id, with prompt and completion price "0".
# A test call returned exactly "yes" with reasoning disabled. With reasoning
# enabled, the 5-token budget was spent on reasoning tokens and content was
# empty, so reasoning stays disabled.
NEMOTRON_MODEL_ID = "nvidia/nemotron-3-ultra-550b-a55b:free"


@dataclass(frozen=True)
class Judge:
    name: str
    label: str
    model: str
    base_url: str
    api_key_env: str
    extra_body: dict
    default_output: str
    local_pricing: bool


JUDGES = {
    "deepseek": Judge(
        name="deepseek",
        label="DeepSeek",
        model=MODEL_ID,
        base_url=BASE_URL,
        api_key_env="DEEPSEEK_API_KEY",
        extra_body={"thinking": {"type": "disabled"}},
        default_output="labels_raw.jsonl",
        local_pricing=True,
    ),
    "nemotron": Judge(
        name="nemotron",
        label="OpenRouter",
        model=NEMOTRON_MODEL_ID,
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        extra_body={
            "reasoning": {"enabled": False},
            "usage": {"include": True},
        },
        default_output="labels_raw_judge2.jsonl",
        local_pricing=False,
    ),
}


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _ceiling() -> float:
    raw = os.environ.get("PILOT_CEILING_USD", "20")
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(
            f"PILOT_CEILING_USD must be a number, got {raw!r}"
        ) from exc
    if value < 0:
        raise SystemExit("PILOT_CEILING_USD must be non-negative")
    return value


def _read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: malformed JSON: {exc}") from exc


def _known_cost(runs_dir: Path) -> float:
    total = 0.0
    for pattern in ("*/answers.jsonl", "*/labels_raw.jsonl"):
        for path in sorted(runs_dir.glob(pattern)):
            for record in _read_jsonl(path):
                value = record.get("cost_usd")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total += float(value)
    return total


def _latest_answers(path: Path) -> dict:
    latest: dict = {}
    for record in _read_jsonl(path):
        pair = (record.get("query_id"), record.get("engine"))
        if not record.get("error"):
            latest[pair] = record
    return latest


def _read_csv(path: Path, required: set[str]) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty CSV or missing header")
        missing = required - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"{path}: missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        return list(reader)


def _normalize(text) -> str:
    if not isinstance(text, str):
        return ""
    return re.sub(r"[^\w\s]", "", text.strip().lower()).strip()


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None) if response is not None else None
    return value if isinstance(value, int) else None


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _call(
    client: OpenAI, prompt: str, judge: Judge
) -> tuple[dict | None, str | None]:
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=judge.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=MAX_OUTPUT_TOKENS,
                extra_body=judge.extra_body,
            )
            return response.model_dump(), None
        except Exception as exc:  # noqa: BLE001 - handled as a failed pair
            status = _status_code(exc)
            if status == 401 or isinstance(exc, AuthenticationError):
                raise SystemExit(
                    f"{judge.label} 401 Unauthorized: check {judge.api_key_env}"
                ) from exc
            if attempt == 0 and (
                status == 429 or isinstance(exc, RateLimitError)
            ):
                delay = _retry_after_seconds(exc)
                if delay is None:
                    delay = 1.0
                time.sleep(delay)
                continue
            return None, f"{type(exc).__name__}: {exc}"
    return None, f"{judge.label} request failed after one retry"


def _extract(data: dict) -> dict:
    choices = data.get("choices") or []
    message = (choices[0].get("message") if choices else {}) or {}
    usage_raw = data.get("usage") or {}
    usage = {
        "input_tokens": usage_raw.get("prompt_tokens"),
        "output_tokens": usage_raw.get("completion_tokens"),
    }
    raw_cost = usage_raw.get("cost")
    if isinstance(raw_cost, (int, float)) and not isinstance(raw_cost, bool):
        usage["cost_usd"] = float(raw_cost)
    return {
        "raw": message.get("content"),
        "model": data.get("model"),
        "fingerprint": data.get("system_fingerprint"),
        "usage": usage,
    }


def _sum_usages(usages: list[dict | None]) -> dict | None:
    if not usages:
        return None
    for usage in usages:
        if not isinstance(usage, dict):
            return None
        if not isinstance(usage.get("input_tokens"), (int, float)) or not isinstance(
            usage.get("output_tokens"), (int, float)
        ):
            return None
    total = {
        "input_tokens": sum(usage["input_tokens"] for usage in usages),
        "output_tokens": sum(usage["output_tokens"] for usage in usages),
    }
    costs = [
        float(usage["cost_usd"])
        for usage in usages
        if isinstance(usage.get("cost_usd"), (int, float))
        and not isinstance(usage.get("cost_usd"), bool)
    ]
    if costs:
        total["cost_usd"] = sum(costs)
    return total


def _judge_cost(judge: Judge, usage: dict | None) -> float | None:
    if judge.local_pricing:
        return prices.classifier_cost(usage)
    # The OpenRouter model is :free (catalog prompt/completion price 0);
    # prefer the cost reported in the response, and fall back to 0.0.
    if isinstance(usage, dict):
        value = usage.get("cost_usd")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _already_labeled(record: dict | None, judge: Judge) -> bool:
    return bool(
        record
        and record.get("classifier_model") == judge.model
        and record.get("prompt_version") == PROMPT_VERSION
    )


def _append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify answers against reference facts (blind)."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument(
        "--queries", default=str(DEFAULT_QUERIES), help="queries.csv path"
    )
    parser.add_argument(
        "--stories", default=str(DEFAULT_STORIES), help="stories.csv path"
    )
    parser.add_argument(
        "--out", default=None, help="labels output path"
    )
    parser.add_argument(
        "--judge",
        choices=sorted(JUDGES),
        default="deepseek",
        help="classifier judge (default: deepseek)",
    )
    args = parser.parse_args(argv)
    judge = JUDGES[args.judge]

    if not os.environ.get(judge.api_key_env):
        print(
            f"{judge.api_key_env} is not set. Load it with: "
            "set -a && source .env && set +a",
            file=sys.stderr,
        )
        return 2

    run_dir = DEFAULT_RUNS_DIR / args.run_id
    answers_path = run_dir / "answers.jsonl"
    output_path = (
        Path(args.out) if args.out else run_dir / judge.default_output
    )

    if not answers_path.exists():
        raise SystemExit(f"answers file not found: {answers_path}")
    if not PROMPT_PATH.exists():
        raise SystemExit(f"prompt file not found: {PROMPT_PATH}")

    template = PROMPT_PATH.read_text(encoding="utf-8")
    answers = _latest_answers(answers_path)

    query_to_story: dict[str, str] = {}
    for row in _read_csv(Path(args.queries), {"query_id", "story_id"}):
        query_id = (row.get("query_id") or "").strip()
        story_id = (row.get("story_id") or "").strip()
        if not query_id or not story_id:
            raise SystemExit(f"{args.queries}: empty query_id or story_id")
        if query_id in query_to_story:
            raise SystemExit(f"{args.queries}: duplicate query_id {query_id!r}")
        query_to_story[query_id] = story_id

    story_to_fact: dict[str, str] = {}
    for row in _read_csv(Path(args.stories), {"story_id", "fact_key"}):
        story_id = (row.get("story_id") or "").strip()
        fact_key = (row.get("fact_key") or "").strip()
        if not story_id or not fact_key:
            raise SystemExit(f"{args.stories}: empty story_id or fact_key")
        if story_id in story_to_fact:
            raise SystemExit(f"{args.stories}: duplicate story_id {story_id!r}")
        story_to_fact[story_id] = fact_key

    existing: dict = {}
    if output_path.exists():
        for record in _read_jsonl(output_path):
            pair = (record.get("query_id"), record.get("engine"))
            existing[pair] = record

    pending: list[tuple[tuple, dict, str]] = []
    skipped = 0
    for pair, answer in answers.items():
        query_id = pair[0]
        current = existing.get(pair)
        if _already_labeled(current, judge):
            skipped += 1
            continue
        if query_id not in query_to_story:
            raise SystemExit(
                f"query_id {query_id!r} is not in {args.queries}"
            )
        story_id = query_to_story[query_id]
        if story_id not in story_to_fact:
            raise SystemExit(
                f"story_id {story_id!r} is not in {args.stories}"
            )
        pending.append((pair, answer, story_to_fact[story_id]))

    if not pending:
        print(f"Nothing to classify (skipped {skipped} already labeled).")
        return 0

    ceiling = _ceiling()
    known_cost = _known_cost(DEFAULT_RUNS_DIR)
    if known_cost >= ceiling:
        print(
            f"STOP: known cost across all runs ${known_cost:.6f} already "
            f"reaches PILOT_CEILING_USD ${ceiling:.2f}.",
            file=sys.stderr,
        )
        return 2

    client = OpenAI(
        api_key=os.environ[judge.api_key_env],
        base_url=judge.base_url,
        max_retries=0,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    failed = 0
    stopped = False
    observed_model: str | None = None
    for pair, answer, fact_key in pending:
        if known_cost >= ceiling:
            print(
                f"STOP: known cost across all runs ${known_cost:.6f} "
                f"reaches PILOT_CEILING_USD ${ceiling:.2f}.",
                file=sys.stderr,
            )
            stopped = True
            break

        prompt = template.format(
            fact_key=fact_key,
            answer_text=answer.get("answer_text") or "",
        )
        usages: list[dict | None] = []
        last: dict | None = None
        label: str | None = None
        for _ in range(2):
            data, error = _call(client, prompt, judge)
            if data is None:
                failed += 1
                print(
                    f"classify: {pair[0]}/{pair[1]}: {error}",
                    file=sys.stderr,
                )
                break
            last = _extract(data)
            if observed_model is None:
                observed_model = last["model"]
            elif last["model"] != observed_model:
                print(
                    "STOP: judge model changed from "
                    f"{observed_model!r} to {last['model']!r}; "
                    "no further calls were made.",
                    file=sys.stderr,
                )
                return 3
            usages.append(last["usage"])
            normalized = _normalize(last["raw"])
            if normalized in VALID_LABELS:
                label = normalized
                break
            label = None

        if last is None:
            continue

        usage = _sum_usages(usages)
        cost = _judge_cost(judge, usage)
        record = {
            "query_id": pair[0],
            "engine": pair[1],
            "judge": judge.name,
            "fact_present": label,
            "raw_output": last["raw"],
            "classifier_model": last["model"],
            "classifier_fingerprint": last["fingerprint"],
            "prompt_version": PROMPT_VERSION,
            "usage": usage,
            "cost_usd": cost,
            "labeled_at": _utc_now(),
        }
        _append(output_path, record)
        written += 1
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            known_cost += float(cost)

    print(
        f"Classified {written} pair(s); skipped {skipped}; failed {failed}; "
        f"known cost across all runs ${known_cost:.6f}"
    )
    if stopped:
        return 2
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
