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
(`output/inc5000_2025_pr.jsonl`), keyed by a hash of its prompt. A re-run reads the
checkpoint and skips rows already done — keying on prompt content (not row position)
means filtering or reordering the input between runs never repeats or mispairs
successful (paid) research.
"""

import csv
import hashlib
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
MAX_RETRIES = 4  # attempts per phase (create / poll) on 429/transient errors
LIMIT = None  # set to an int to process only the first N rows (testing)


def _is_rate_limit(exc):
    """True if `exc` looks like a Parallel rate-limit (HTTP 429). Checked by
    status/message rather than a concrete exception type so we don't depend on the
    SDK's internal exception module, which can move between releases."""
    return getattr(exc, "status_code", None) == 429 or "rate limit" in str(exc).lower()


def _backoff(attempt, exc):
    """Seconds to wait before the next retry — extra patience on a rate limit."""
    return 2**attempt + (10 if _is_rate_limit(exc) else 0)


def _key(prompt):
    """Stable per-row identity for the checkpoint: a hash of the prompt text. Keying
    on content (not row position) keeps resume correct even when the input CSV is
    filtered or reordered between runs."""
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()


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
    """Pull the answer text out of a TaskRunResult (text output schema). An empty
    string is a valid answer, so only fall back to a repr when content is truly
    absent (None)."""
    content = getattr(result.output, "content", None)
    return content if content is not None else str(result.output)


def _research_one(client, limiter, prompt):
    """Create one run, then poll its result, returning the answer text. Creation (a
    paid POST) and polling (a free GET) are retried *separately*: a flaky poll
    re-reads the same run_id instead of creating — and re-billing — a new run. After
    MAX_RETRIES a phase degrades to an 'ERROR: ...' string so the row still completes
    (and is checkpointed) instead of sinking the whole batch."""
    last_exc = None

    run = None
    for attempt in range(MAX_RETRIES):
        try:
            limiter.acquire()
            run = client.task_run.create(
                input=prompt,
                task_spec={"output_schema": OUTPUT_SCHEMA},
                processor=PROCESSOR,
            )
            break
        except Exception as exc:  # any failure should retry then degrade gracefully
            last_exc = exc
            print(f"  create {attempt + 1}/{MAX_RETRIES} failed: {str(exc)[:160]}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(_backoff(attempt, exc))
    if run is None:
        return f"ERROR: {last_exc}"

    for attempt in range(MAX_RETRIES):
        try:
            result = client.task_run.result(run.run_id, api_timeout=RESULT_TIMEOUT)
            return _output_text(result)
        except Exception as exc:  # re-poll the same run_id; this never re-bills
            last_exc = exc
            print(f"  poll {attempt + 1}/{MAX_RETRIES} failed: {str(exc)[:160]}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(_backoff(attempt, exc))
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
    """Read already-completed {prompt key -> result} from the JSONL checkpoint. A
    malformed *final* line is tolerated silently (a run killed mid-write); an earlier
    malformed line is unexpected, so warn rather than hide possible corruption."""
    done = {}
    if not os.path.exists(CHECKPOINT):
        return done
    with open(CHECKPOINT, encoding="utf-8") as f:
        lines = f.readlines()
    last = len(lines) - 1
    for idx, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            done[rec["key"]] = rec["result"]
        except (json.JSONDecodeError, KeyError):
            if idx != last:
                print(
                    f"  warning: skipping malformed checkpoint line {idx + 1}",
                    file=sys.stderr,
                )
    return done


def main():
    rows, fieldnames = _load_rows()
    done = _load_checkpoint()
    todo = [i for i, row in enumerate(rows) if _key(row[PROMPT_COL]) not in done]
    total = len(rows)
    print(f"{total} rows, {total - len(todo)} already done, {len(todo)} to research")
    if LIMIT is not None:
        print(f"  LIMIT={LIMIT}: {OUTPUT} will hold only these {total} rows")

    if todo:
        client = Parallel()  # reads PARALLEL_API_KEY from the environment
        limiter = _RateLimiter(RATE_PER_MIN)
        os.makedirs(os.path.dirname(CHECKPOINT), exist_ok=True)
        completed = total - len(todo)

        # The checkpoint is written only here, on the main thread, as each future
        # resolves — workers never touch the file, so no lock is needed.
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
                done[_key(rows[i][PROMPT_COL])] = text
                ckpt.write(
                    json.dumps({"key": _key(rows[i][PROMPT_COL]), "result": text})
                    + "\n"
                )
                ckpt.flush()
                completed += 1
                company = rows[i].get("company", "?")
                print(f"  [{completed}/{total}] {company}")

    out_fields = fieldnames + ([RESULT_COL] if RESULT_COL not in fieldnames else [])
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for row in rows:
            row[RESULT_COL] = done.get(_key(row[PROMPT_COL]), "")
            writer.writerow(row)
    print(f"Done: {total} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
