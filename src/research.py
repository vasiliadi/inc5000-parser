"""
Research the filtered Inc. 5000 companies with the Parallel Task API.

Reads `output/inc5000_2025.csv`, runs each row's ready-made `prompt` through
a Parallel Task run (a web-research agent), and writes the answer into a new
`result` column in `output/inc5000_2025_pr.csv` (the source CSV is untouched).

The Task API rate limit is 2000 POST /v1/tasks/runs per minute; only *creating* a
run counts, GET/result polling does not. We stay well under it: a thread-safe
limiter spaces out `create()` calls (RATE_PER_MIN) and a bounded ThreadPoolExecutor
(MAX_WORKERS) caps how many runs are in flight. Each worker creates a run and then
long-polls its result (a GET, so free), which is simpler than a two-phase
create-all-then-poll and is fully resumable.

Resumable: every finished row is appended to a JSONL checkpoint
(`output/inc5000_2025_pr.jsonl`). A re-run reads the checkpoint and skips rows
already done, so a crash or Ctrl-C never repeats successful (paid) research.
"""

import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from parallel import Parallel

INPUT = "output/inc5000_2025.csv"
OUTPUT = "output/inc5000_2025_pr.csv"
CHECKPOINT = "output/inc5000_2025_pr.jsonl"  # resume log, one JSON object/line

PROMPT_COL = "prompt"
RESULT_COL = "result"

PROCESSOR = "lite"  # cheapest Parallel tier; enough for a one-line summary
OUTPUT_SCHEMA = {"type": "text"}  # free-text answer; the prompt carries the task

MAX_WORKERS = 25  # concurrent in-flight runs
RATE_PER_MIN = 1500  # create() cap, safely below the 2000/min hard limit
RESULT_TIMEOUT = 600  # seconds to long-poll one run's result
MAX_RETRIES = 4  # per-row create+result attempts on 429/transient errors
LIMIT = None  # set to an int to process only the first N rows (testing)


def _is_rate_limit(exc):
    """True if `exc` looks like a Parallel rate-limit (HTTP 429). Checked by
    status/message rather than a concrete exception type so we don't depend on the
    SDK's internal exception module, which can move between releases."""
    return getattr(exc, "status_code", None) == 429 or "rate limit" in str(exc).lower()


class _RateLimiter:
    """Spaces out create() calls to at most `per_min` per minute across threads."""

    def __init__(self, per_min):
        self._min_interval = 60.0 / per_min
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self):
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            start = max(now, self._next_at)
            self._next_at = start + self._min_interval
        if wait > 0:
            time.sleep(wait)


def _output_text(result):
    """Pull the answer text out of a TaskRunResult (text output schema)."""
    output = result.output
    return getattr(output, "content", None) or str(output)


def _research_one(client, limiter, prompt):
    """Create one run and return its result text, retrying on 429/transient errors.
    After MAX_RETRIES it returns an 'ERROR: ...' string so the row still completes
    (and is checkpointed) instead of sinking the whole run."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            limiter.acquire()
            run = client.task_run.create(
                input=prompt,
                task_spec={"output_schema": OUTPUT_SCHEMA},
                processor=PROCESSOR,
            )
            result = client.task_run.result(run.run_id, api_timeout=RESULT_TIMEOUT)
            return _output_text(result)
        except Exception as exc:  # any failure should retry then degrade gracefully
            last_exc = exc
            backoff = 2**attempt + (10 if _is_rate_limit(exc) else 0)
            print(f"  attempt {attempt + 1}/{MAX_RETRIES} failed: {str(exc)[:160]}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(backoff)
    return f"ERROR: {last_exc}"


def _load_rows():
    """Read INPUT (BOM-prefixed) into a list of dict rows + the header order."""
    with open(INPUT, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None or PROMPT_COL not in reader.fieldnames:
            sys.exit(f"{INPUT} has no '{PROMPT_COL}' column.")
        rows = list(reader)
        fieldnames = list(reader.fieldnames)
    if LIMIT is not None:
        rows = rows[:LIMIT]
    return rows, fieldnames


def _load_checkpoint():
    """Read already-completed {index -> result} from the JSONL checkpoint."""
    done = {}
    if not os.path.exists(CHECKPOINT):
        return done
    with open(CHECKPOINT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[int(rec["i"])] = rec["result"]
            except (json.JSONDecodeError, KeyError, ValueError):
                continue  # ignore a half-written trailing line
    return done


def main():
    rows, fieldnames = _load_rows()
    done = _load_checkpoint()
    todo = [i for i in range(len(rows)) if i not in done]
    total = len(rows)
    print(f"{total} rows, {len(done)} already done, {len(todo)} to research")

    if todo:
        client = Parallel()  # reads PARALLEL_API_KEY from the environment
        limiter = _RateLimiter(RATE_PER_MIN)
        write_lock = threading.Lock()
        os.makedirs(os.path.dirname(CHECKPOINT), exist_ok=True)
        completed = len(done)

        with (
            open(CHECKPOINT, "a", encoding="utf-8") as ckpt,
            ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool,
        ):
            futures = {
                pool.submit(_research_one, client, limiter, rows[i][PROMPT_COL]): i
                for i in todo
            }
            for fut in as_completed(futures):
                i = futures[fut]
                text = fut.result()
                done[i] = text
                with write_lock:
                    ckpt.write(json.dumps({"i": i, "result": text}) + "\n")
                    ckpt.flush()
                completed += 1
                company = rows[i].get("company", "?")
                print(f"  [{completed}/{total}] {company}")

    out_fields = fieldnames + ([RESULT_COL] if RESULT_COL not in fieldnames else [])
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            row[RESULT_COL] = done.get(i, "")
            writer.writerow(row)
    print(f"Done: {total} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
