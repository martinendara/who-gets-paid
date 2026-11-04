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
from src.engines import claude, gemini, openai_engine, perplexity

ENGINES = {
    "gemini": gemini.ask,
    "openai": openai_engine.ask,
    "claude": claude.ask,
    "perplexity": perplexity.ask,
}
DEFAULT_RUN_ID_PREFIX = "run-"
SMOKE_PROJECTION_N = 160
SMOKE_PROJECTION_LIMIT_USD = 15.0


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


def _new_state(*, track_pairs: bool) -> dict:
    state = {
        "lines": 0,
        "errors": 0,
        "known_cost": 0.0,
        "unknown_cost": 0,
        "searches": 0,
        "gemini_searches": 0,
        "answers_by_engine": {},
        "known_cost_by_engine": {},
        "known_cost_n_by_engine": {},
    }
    if track_pairs:
        state["done_pairs"] = set()
        state["seen_pairs"] = set()
    return state


def _accumulate_record(state: dict, record: dict, *, track_pairs: bool) -> None:
    engine = record.get("engine")
    state["lines"] += 1

    if track_pairs:
        pair = (record.get("query_id"), engine)
        state["seen_pairs"].add(pair)
        # A pair is done when it has at least one error-free record. Error
        # records stay in the file as history; metrics must use the latest
        # error-free record for each pair.
        if not record.get("error"):
            state["done_pairs"].add(pair)

    if record.get("error"):
        state["errors"] += 1

    search_count = record.get("search_count")
    if isinstance(search_count, (int, float)):
        state["searches"] += search_count
        if engine == "gemini":
            state["gemini_searches"] += search_count

    state["answers_by_engine"][engine] = (
        state["answers_by_engine"].get(engine, 0) + 1
    )

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


def _read_run_file(path: Path) -> dict:
    state = _new_state(track_pairs=True)
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
            _accumulate_record(state, record, track_pairs=True)
    return state


def _read_all_runs(runs_dir: Path) -> dict:
    state = _new_state(track_pairs=False)
    if not runs_dir.exists():
        return state

    for path in sorted(runs_dir.glob("*/answers.jsonl")):
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(
                        f"{path}:{line_no}: malformed JSON: {exc}"
                    ) from exc
                _accumulate_record(state, record, track_pairs=False)
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


def _print_summary(
    current: dict, global_state: dict, new_writes: int, retried: int
) -> None:
    print(
        f"Answers written (this run): {current['lines']} "
        f"({new_writes} new, {retried} retried)"
    )
    print(f"Errors (this run): {current['errors']}")
    print(f"Total cost (all runs, known): ${global_state['known_cost']:.6f}")
    print(f"This run cost (known): ${current['known_cost']:.6f}")
    if global_state["unknown_cost"]:
        print(
            "Records with unknown cost (all runs): "
            f"{global_state['unknown_cost']}"
        )
    print(f"Total searches (all runs): {global_state['searches']}")
    if global_state["gemini_searches"] > prices.GEMINI_SEARCH_FREE_REQUESTS_PER_MONTH:
        print(
            "WARNING: Gemini searches across all runs exceed the 5,000/month "
            "free allowance; search overage is not included in the known cost.",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run raw queries through the configured engines."
    )
    parser.add_argument("--file", required=True, help="CSV with query_id,text")
    parser.add_argument(
        "--engines", required=True, help="Comma-separated engines: gemini,openai,claude,perplexity"
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

    current = _read_run_file(output_path)
    global_state = _read_all_runs(Path("runs"))
    new_writes = 0
    retried = 0
    ceiling_hit = False

    if global_state["known_cost"] >= ceiling:
        print(
            f"STOP: known cost across all runs "
            f"${global_state['known_cost']:.6f} already reaches "
            f"PILOT_CEILING_USD ${ceiling:.2f}.",
            file=sys.stderr,
        )
        _print_summary(current, global_state, new_writes, retried)
        return 2

    for query in queries:
        if ceiling_hit:
            break
        for engine_name in engine_names:
            pair = (query["query_id"], engine_name)
            if pair in current["done_pairs"]:
                continue

            is_retry = pair in current["seen_pairs"]
            result = ENGINES[engine_name](query["text"])
            record = {
                "query_id": query["query_id"],
                **result,
                "run_at": _utc_now(),
            }
            record_cost = prices.cost(result)
            record["cost_usd"] = record_cost
            _append_record(output_path, record)

            _accumulate_record(current, record, track_pairs=True)
            _accumulate_record(global_state, record, track_pairs=False)
            new_writes += 1
            if is_retry:
                retried += 1

            if global_state["known_cost"] > ceiling:
                ceiling_hit = True
                print(
                    f"STOP: known cost across all runs "
                    f"${global_state['known_cost']:.6f} exceeds "
                    f"PILOT_CEILING_USD ${ceiling:.2f}. No further calls were made.",
                    file=sys.stderr,
                )
                break

    _print_summary(current, global_state, new_writes, retried)

    is_smoke = run_id == "smoke" or input_path.name == "smoke.csv"
    total_projection = None
    if is_smoke:
        projections: dict[str, float | None] = {}
        missing: list[str] = []
        for engine_name in engine_names:
            n = current["known_cost_n_by_engine"].get(engine_name, 0)
            if not n:
                print(f"{engine_name} cost per question: null (n=0)")
                print(f"{engine_name} projection x{SMOKE_PROJECTION_N}: null")
                missing.append(engine_name)
                projections[engine_name] = None
                continue
            per_question = (
                current["known_cost_by_engine"].get(engine_name, 0.0) / n
            )
            projection = per_question * SMOKE_PROJECTION_N
            projections[engine_name] = projection
            print(
                f"{engine_name} cost per question: ${per_question:.6f} (n={n})"
            )
            print(
                f"{engine_name} projection x{SMOKE_PROJECTION_N}: "
                f"${projection:.2f}"
            )
        if missing:
            print(
                f"Total projection x{SMOKE_PROJECTION_N}: null "
                f"(no known cost for: {', '.join(missing)})"
            )
        else:
            total_projection = sum(
                projection
                for projection in projections.values()
                if projection is not None
            )
            print(
                f"Total projection x{SMOKE_PROJECTION_N}: "
                f"${total_projection:.2f}"
            )
            if total_projection > SMOKE_PROJECTION_LIMIT_USD:
                print(
                    f"STOP: projected cost ${total_projection:.2f} for "
                    f"{SMOKE_PROJECTION_N} questions exceeds "
                    f"USD {SMOKE_PROJECTION_LIMIT_USD:.2f}.",
                    file=sys.stderr,
                )

    if ceiling_hit:
        return 2
    if (
        total_projection is not None
        and total_projection > SMOKE_PROJECTION_LIMIT_USD
    ):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
