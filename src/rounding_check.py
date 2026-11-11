"""Re-check human ``wrong`` labels for rounded figures (D78).

Every human ``wrong`` label in ``review_<run_id>.csv`` is re-checked by the
first classifier with a single question (``prompts/rounding_check_v1.md``):
whether the only difference from the reference fact is a rounded or
approximated figure. A ``yes`` turns the label into ``yes``; anything else
leaves the human label standing.
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

from src import classify

DEFAULT_RUNS_DIR = Path("runs")
DEFAULT_REVIEW_DIR = Path("data/private")
PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "prompts" / "rounding_check_v1.md"
)
PROMPT_VERSION = PROMPT_PATH.stem
OUTPUT_FILE = "rounding_check.jsonl"
JUDGE = classify.JUDGES["deepseek"]
VALID_OUTPUTS = {"yes", "no"}
REQUIRED_FIELDS = {"review_id", "fact_key", "answer_text", "human_fact_present"}
BACKUP_SUFFIX = ".before_rounding.csv"


def _read_review(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"{path}: empty CSV or missing header")
        missing = REQUIRED_FIELDS - set(reader.fieldnames)
        if missing:
            raise SystemExit(
                f"{path}: missing required column(s): "
                f"{', '.join(sorted(missing))}"
            )
        rows = list(reader)
    seen: set[str] = set()
    for row in rows:
        review_id = (row.get("review_id") or "").strip()
        if not review_id:
            raise SystemExit(f"{path}: empty review_id")
        if review_id in seen:
            raise SystemExit(f"{path}: duplicate review_id {review_id!r}")
        seen.add(review_id)
    return list(reader.fieldnames), rows


def select_wrong(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if (row.get("human_fact_present") or "").strip() == "wrong"
    ]


def _line_terminator(path: Path) -> str:
    """Return the line terminator used by the file (csv defaults to CRLF)."""
    with path.open("rb") as handle:
        head = handle.read(65536)
    return "\r\n" if b"\r\n" in head else "\n"


def rewrite(
    path: Path, fieldnames: list[str], rows: list[dict], decided: dict[str, str | None]
) -> None:
    """Write the review sheet back, turning ``yes`` checks into ``yes`` labels."""
    for row in rows:
        review_id = (row.get("review_id") or "").strip()
        if decided.get(review_id) == "yes":
            row["human_fact_present"] = "yes"
    terminator = _line_terminator(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, lineterminator=terminator
        )
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def _check(client, prompt: str) -> dict:
    """Run the rounding question; invalid output gets one retry, then null."""
    usages: list[dict | None] = []
    last: dict | None = None
    output: str | None = None
    for attempt in range(2):
        data, error = classify._call(client, prompt, JUDGE)
        if data is None:
            if error:
                print(f"rounding_check: {error}", file=sys.stderr)
            break
        last = classify._extract(data)
        usages.append(last["usage"])
        normalized = classify._normalize(last["raw"])
        if normalized in VALID_OUTPUTS:
            output = normalized
            break
        output = None
    usage = classify._sum_usages(usages)
    cost = None if usage is None else classify._judge_cost(JUDGE, usage)
    return {
        "check_output": output,
        "raw_output": None if last is None else last["raw"],
        "model": JUDGE.model if last is None else last.get("model"),
        "fingerprint": None if last is None else last.get("fingerprint"),
        "usage": usage,
        "cost_usd": cost,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-check human 'wrong' labels for rounded figures (D78)."
    )
    parser.add_argument("--run-id", required=True, help="Run identifier")
    args = parser.parse_args(argv)

    review_path = DEFAULT_REVIEW_DIR / f"review_{args.run_id}.csv"
    backup_path = DEFAULT_REVIEW_DIR / (
        f"review_{args.run_id}{BACKUP_SUFFIX}"
    )
    out_path = DEFAULT_RUNS_DIR / args.run_id / OUTPUT_FILE

    if not review_path.exists():
        raise SystemExit(f"review file not found: {review_path}")
    if not PROMPT_PATH.exists():
        raise SystemExit(f"prompt file not found: {PROMPT_PATH}")
    if backup_path.exists():
        print(
            f"STOP: {backup_path} already exists; the check is not run twice.",
            file=sys.stderr,
        )
        return 4

    fieldnames, rows = _read_review(review_path)
    selected = select_wrong(rows)
    if not selected:
        print(
            "Reviewed 0 wrong label(s); moved to yes: 0; stayed wrong: 0; "
            "null: 0"
        )
        return 0

    if not os.environ.get(JUDGE.api_key_env):
        print(
            f"{JUDGE.api_key_env} is not set. Load it with: "
            "set -a && source .env && set +a",
            file=sys.stderr,
        )
        return 2

    template = PROMPT_PATH.read_text(encoding="utf-8")
    client = classify.OpenAI(
        api_key=os.environ[JUDGE.api_key_env],
        base_url=JUDGE.base_url,
        max_retries=0,
    )

    shutil.copyfile(review_path, backup_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    decided: dict[str, str | None] = {}
    moved = 0
    stayed = 0
    nulls = 0
    for row in selected:
        review_id = (row.get("review_id") or "").strip()
        prompt = template.format(
            fact_key=row.get("fact_key") or "",
            answer_text=row.get("answer_text") or "",
        )
        result = _check(client, prompt)
        decided[review_id] = result["check_output"]
        if result["check_output"] == "yes":
            moved += 1
        elif result["check_output"] == "no":
            stayed += 1
        else:
            nulls += 1
        record = {
            "review_id": review_id,
            "human_original": "wrong",
            **result,
            "prompt_version": PROMPT_VERSION,
            "checked_at": classify._utc_now(),
        }
        classify._append(out_path, record)

    rewrite(review_path, fieldnames, rows, decided)
    print(
        f"Reviewed {len(selected)} wrong label(s); moved to yes: {moved}; "
        f"stayed wrong: {stayed}; null: {nulls}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
