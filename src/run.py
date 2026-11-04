"""Run raw queries through the configured engines and store normalized records."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from src import prices
from src.engines import claude, gemini

ENGINES = {
    "gemini": gemini.ask,
    "claude": claude.ask,
}
DEFAULT_RUN_ID_PREFIX = "run-"
SMOKE_PROJECTION_N = 160
SMOKE_PROJECTION_LIMIT_USD = 10.0


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _default_run_id() -> str:
    return DEFAULT_RUN_ID_PREFIX + datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def _read_queries(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty CSV or missing header")
        missing = {"query_id", "text"} - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"{path}: missing required column(s): {', '.join(sorted(missing))}"
            )

        rows: list[dict] = []
        seen: set[str] = set()
        for line_no, row in enumerate(reader, start=2):
            query_id = (row.get("query_id") or "").strip()
            text = (row.get("text") or "").strip()
            if not query_id or not text:
                raise SystemExit(
                    f"{path}:{line_no}: query_id and text must be non-empty"
                )
            if query_id in seen:
                raise SystemExit(
                    f"{path}:{line_no}: duplicate query_id {query_id!r}"
                )
            seen.add(query_id)
            rows.append({"query_id": query_id, "text": text})
        return rows


def _parse_engines(value: str) -> list[str]:
    engines: list[str] = []
    for part in value.split(","):
        name = part.strip().lower()
        if not name:
            continue
        if name not in ENGINES:
            raise SystemExit(
                f"unknown engine {name!r}; available: {', '.join(sorted(ENGINES))}"
            )
        if name not in engines:
            engines.append(name)
    if not engines:
        raise SystemExit("--engines must name at least one engine")
    return engines


def _read_existing(path: Path) -> dict:
    state = {
        "pairs": set(),
        "lines": 0,
        "errors": 0,
        "known_cost": 0.0,
        "unknown_cost": 0,
        "searches": 0,
        "answers_by_engine": {},
        "known_cost_by_engine": {},
        "known_cost_n_by_engine": {},
        "gemini_searches": 0,
    }
    if not path.exists():
        return state

    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: malformed JSON: {exc}") from exc

            state["lines"] += 1
            engine = record.get("engine")
            state["pairs"].add((record.get("query_id"), engine))
            state["answers_by_engine"][engine] = (
                state["answers_by_engine"].get(engine, 0) + 1
            )

            if record.get("error"):
                state["errors"] += 1

            search_count = record.get("search_count")
            if isinstance(search_count, (int, float)):
                state["searches"] += search_count
                if engine == "gemini":
                    state["gemini_searches"] += search_count

            record_cost = record.get("cost_usd")
            if isinstance(record_cost, (int, float)):
                state["known_cost"] += record_cost
                state["known_cost_by_engine"][engine] = (
                    state["known_cost_by_engine"].get(engine, 0.0) + record_cost
                )
                state["known_cost_n_by_engine"][engine] = (
                    state["known_cost_n_by_engine"].get(engine, 0) + 1
                )
            else:
                state["unknown_cost"] += 1

    return state


def _append_record(path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _ceiling() -> float:
    raw = os.environ.get("PILOT_CEILING_USD", "20")
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"PILOT_CEILING_USD must be a number, got {raw!r}") from exc
    if value < 0:
        raise SystemExit("PILOT_CEILING_USD must be non-negative")
    return value


def _print_summary(state: dict, new_writes: int) -> None:
    print(f"Answers written: {state['lines']} ({new_writes} new)")
    print(f"Errors: {state['errors']}")
    print(f"Total cost (known): ${state['known_cost']:.6f}")
    if state["unknown_cost"]:
        print(f"Records with unknown cost: {state['unknown_cost']}")
    print(f"Total searches: {state['searches']}")
    if state["gemini_searches"] > prices.GEMINI_SEARCH_FREE_REQUESTS_PER_MONTH:
        print(
            "WARNING: Gemini searches exceed the 5,000/month free allowance; "
            "search overage is not included in the known cost.",
            file=sys.stderr,
        )


def _smoke_projection(state: dict) -> float | None:
    n = state["known_cost_n_by_engine"].get("claude", 0)
    if not n:
        return None
    per_question = state["known_cost_by_engine"].get("claude", 0.0) / n
    return per_question * SMOKE_PROJECTION_N


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run raw queries through the configured engines."
    )
    parser.add_argument("--file", required=True, help="CSV with query_id,text")
    parser.add_argument(
        "--engines", required=True, help="Comma-separated engines: gemini,claude"
    )
    parser.add_argument("--run-id", default=None, help="Run identifier")
    args = parser.parse_args(argv)

    input_path = Path(args.file)
    if not input_path.exists():
        raise SystemExit(f"input file not found: {input_path}")

    queries = _read_queries(input_path)
    engine_names = _parse_engines(args.engines)
    run_id = args.run_id or _default_run_id()
    ceiling = _ceiling()

    run_dir = Path("runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "answers.jsonl"

    state = _read_existing(output_path)
    new_writes = 0
    ceiling_hit = False

    if state["known_cost"] >= ceiling:
        print(
            f"STOP: existing known cost ${state['known_cost']:.6f} already reaches "
            f"PILOT_CEILING_USD ${ceiling:.2f}.",
            file=sys.stderr,
        )
        _print_summary(state, new_writes)
        return 2

    for query in queries:
        if ceiling_hit:
            break
        for engine_name in engine_names:
            pair = (query["query_id"], engine_name)
            if pair in state["pairs"]:
                continue

            result = ENGINES[engine_name](query["text"])
            record = {
                "query_id": query["query_id"],
                **result,
                "run_at": _utc_now(),
            }
            record_cost = prices.cost(result)
            record["cost_usd"] = record_cost
            _append_record(output_path, record)

            state["pairs"].add(pair)
            state["lines"] += 1
            new_writes += 1
            state["answers_by_engine"][engine_name] = (
                state["answers_by_engine"].get(engine_name, 0) + 1
            )
            if record.get("error"):
                state["errors"] += 1

            search_count = record.get("search_count")
            if isinstance(search_count, (int, float)):
                state["searches"] += search_count
                if engine_name == "gemini":
                    state["gemini_searches"] += search_count

            if record_cost is None:
                state["unknown_cost"] += 1
            else:
                state["known_cost"] += record_cost
                state["known_cost_by_engine"][engine_name] = (
                    state["known_cost_by_engine"].get(engine_name, 0.0) + record_cost
                )
                state["known_cost_n_by_engine"][engine_name] = (
                    state["known_cost_n_by_engine"].get(engine_name, 0) + 1
                )

            if state["known_cost"] > ceiling:
                ceiling_hit = True
                print(
                    f"STOP: known cost ${state['known_cost']:.6f} exceeds "
                    f"PILOT_CEILING_USD ${ceiling:.2f}. No further calls were made.",
                    file=sys.stderr,
                )
                break

    _print_summary(state, new_writes)

    is_smoke = run_id == "smoke" or input_path.name == "smoke.csv"
    if is_smoke:
        projection = _smoke_projection(state)
        n = state["known_cost_n_by_engine"].get("claude", 0)
        if projection is None:
            print("Claude cost per question: null (no Claude answer with known cost)")
            print(f"Projection x{SMOKE_PROJECTION_N}: null")
        else:
            per_question = projection / SMOKE_PROJECTION_N
            print(f"Claude cost per question: ${per_question:.6f} (n={n})")
            print(f"Projection x{SMOKE_PROJECTION_N}: ${projection:.2f}")
            if projection > SMOKE_PROJECTION_LIMIT_USD:
                print(
                    f"STOP: projected Claude cost ${projection:.2f} for "
                    f"{SMOKE_PROJECTION_N} questions exceeds "
                    f"USD {SMOKE_PROJECTION_LIMIT_USD:.2f}. Switch to "
                    "claude-haiku-5-5 before the first real query, or raise the "
                    "budget explicitly.",
                    file=sys.stderr,
                )

    if ceiling_hit:
        return 2
    if is_smoke:
        projection = _smoke_projection(state)
        if projection is not None and projection > SMOKE_PROJECTION_LIMIT_USD:
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
