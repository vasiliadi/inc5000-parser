# Researcher (`src/research.py`)

Enriches a company CSV by running each row's prompt through the
**[Parallel Task API](https://docs.parallel.ai/task-api/task-quickstart)** (a web-research
agent) and writing the answer into a new `result` column. Read before changing
`src/research.py` or retuning the concurrency/rate knobs.

## What it does

Reads `output/inc5000_2025.csv`, sends each row's `prompt` to a Parallel Task run, and
writes `output/inc5000_2025_pr.csv` = the original columns + an appended `result` column.
The source CSV is never modified.

The run input *is* the row's `prompt`, and the output schema is just `{"type": "text"}` — no
extra prompt engineering in the script. Each `prompt` carries the full instruction per row
(e.g. *"find the company X on the internet and describe what business they are in and what
problem they solve"*). The raw scraper output has **no** `prompt` column, so `_load_rows`
exits with `… has no 'prompt' column.` until the user adds one — preparing that column (and
filtering the rows) is a manual step done before running, covered in the README.

Configure with the `PARALLEL_API_KEY` environment variable (already in `.env`). In a shell
without direnv active, load it explicitly: `uv run --env-file .env src/research.py`.

## Architecture notes

The Parallel SDK splits a task into two calls — `client.task_run.create(...)` (a POST that
**creates** the run) and `client.task_run.result(run_id, api_timeout=...)` (a **long-poll
GET** that waits for it to finish). Each worker does both back-to-back.

- **Concurrency:** a `ThreadPoolExecutor(MAX_WORKERS)` runs many create+result pairs at
  once. `task_run.result()` blocks server-side, so threads (not async) keep it simple.
- **Rate limiting:** only `create()` counts against the API limit (2000/min — see
  [rate limits](https://docs.parallel.ai/getting-started/rate-limits)); GET/result polling
  is free. `_RateLimiter` spaces out `create()` calls to `RATE_PER_MIN` across all threads
  via a lock + next-allowed timestamp, so the cap holds regardless of input size or worker
  count. This is why create+result-per-worker is preferred over a two-phase
  create-all-then-poll: simpler, and the limiter already guarantees the constraint.
- **Resumability:** every finished row is appended (and flushed) to a JSONL checkpoint
  `output/inc5000_2025_pr.jsonl` as `{"key": <sha1 of the prompt>, "result": <text>}`. Keying
  on prompt *content* (via `_key`), not row position, is what lets you filter or reorder the
  input between runs without mispairing or repeating done rows. The checkpoint is written
  only on the main thread as each future resolves, so it needs no lock. `_load_checkpoint`
  warns on a malformed non-final line and tolerates a half-written final one. Delete the
  JSONL to force a clean re-run.
- **Graceful degradation:** `_research_one` creates the run and polls its result as **two
  separately-retried phases** — so a flaky poll re-reads the same `run_id` (free) instead of
  re-creating a new, re-billed run. Both phases back off (`_is_rate_limit` checks HTTP 429 by
  status/message, same pattern as the scraper, and earns extra wait). After `MAX_RETRIES` a
  phase returns an `"ERROR: ..."` string instead of raising, so one bad row still gets
  checkpointed and the batch finishes.

## Knobs (top of `src/research.py`)

- `PROCESSOR` — Parallel tier; `"lite"` is the cheapest and is enough for a one-line
  summary. If the API ever rejects `"lite"` for the Task endpoint, the fallback is
  `"lite-fast"`.
- `MAX_WORKERS` — concurrent in-flight runs.
- `RATE_PER_MIN` — `create()` ceiling; kept safely below the 2000/min hard limit.
- `RESULT_TIMEOUT` — seconds to long-poll one run's result.
- `MAX_RETRIES` — attempts per phase (create, then poll) before degrading to `ERROR:`.
- `LIMIT` — set to an int to process only the first N rows (smoke testing); `None` = all.

## Gotchas

- **`_load_rows` opens with `encoding="utf-8-sig"`** so a BOM-prefixed export (some tools
  add one) doesn't turn the first header into `﻿rank`. Harmless on BOM-free files too.
- **Every run is paid — one Parallel run per row.** Filtering `inc5000_2025.csv` down to the
  rows you actually want before adding the `prompt` column keeps the cost (and time) in
  check. The checkpoint makes interrupt/resume safe, but deleting the JSONL re-pays for
  every row.
- **Output text lives at `result.output.content`** for a text schema; `_output_text` returns
  it, falling back to `str(output)` only when content is truly absent (`None`) — an empty
  string is a valid answer and is kept as-is.
- **`LIMIT` truncates the output.** A capped run writes only those N rows to `OUTPUT`,
  overwriting any previous full file (the script prints a `LIMIT=…` warning). Use it for
  smoke tests, then clear `LIMIT` for the real run.
