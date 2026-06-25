# inc5000 parser

![Python](https://img.shields.io/badge/Python-3.12-blue)

Single-file scraper that extracts the Inc. 5000 (2025) company list from `https://www.inc.com/inc5000/2025` into a CSV. The site is a Next.js App Router SPA with no static HTML or JSON to fetch, and it blocks local headless browsers almost immediately — so the scraper uses [`firecrawl-py`](https://github.com/firecrawl/firecrawl) to render the page server-side and read the table out of the result.

## Setup & Run

```bash
uv sync
cp env.example .env               # then fill in your API keys (see env.example)
uv run --env-file .env src/scraper.py
```

If you use [direnv](https://direnv.net/), the bundled `.envrc` auto-loads `.env` and you can drop the `--env-file .env` flag.

By default it scrapes the **entire** list (~5000 companies) into `inc5000_2025.csv` (see `OUTPUT`). It does this with a firecrawl **persistent browser session**: one session navigates once, bumps the pager to 50 rows/page, then walks the client-side pagination in batches (each firecrawl `execute` call has a stdout-size limit, so `PAGES_PER_CALL` caps how many pages one batch returns and the driver loops until the list ends). Rows are deduped on `rank|company`.

## Columns

RANK, COMPANY, 3-YEAR GROWTH, REVENUE RANGE, EMPLOYEE GROWTH, YEAR FOUNDED, INDUSTRY, CITY, STATE.

Revenue Range, Employee Growth, and Year Founded are paywalled — for non-subscribers these cells are blank / show a 🔒, so those CSV fields come out empty.

## Analysis

`src/analyzer.py` is an interactive [marimo](https://marimo.io/) notebook for exploring the scraped CSV:

```bash
uv run marimo run src/analyzer.py    # read-only app
uv run marimo edit src/analyzer.py   # interactive editor
```

It loads `output/inc5000_2025.csv`, **auto-drops empty columns** (the three paywalled fields above), and parses `3-YEAR GROWTH` (`"37,364%"`) into a number. It then flags `growth_3yr` outliers **per group** with `IsolationForest` — each company is scored relative to its own `industry` / `city` / `state` (pick the dimension from a dropdown; tune contamination and minimum group size with sliders). Outliers are kept and highlighted, not removed. Four views update reactively: a growth box plot per group, a top-groups bar chart (count + mean/median), an outlier-colored strip plot, and a sortable summary table.

## Research

`src/researcher.py` enriches a company list by running each row's `prompt` through the [Parallel Task API](https://docs.parallel.ai/task-api/task-quickstart) (a web-research agent):

```bash
uv run --env-file .env src/researcher.py   # needs PARALLEL_API_KEY in .env (drop the flag if direnv loads it)
```

It reads `output/inc5000_2025.csv` and writes `output/inc5000_2025_pr.csv` — the same rows plus an appended `result` column holding each company's researched summary (the source CSV is untouched). Each `prompt` cell is the full instruction for its row, so the script just forwards it as the task input.

**First, prepare the input.** The scraper's `inc5000_2025.csv` has no `prompt` column, so the run won't start until you add one. **Edit/filter `inc5000_2025.csv` down to the rows you want and add a `prompt` column** with the instruction to research per row (e.g. `find the company {name} on the internet and describe what business they are in and what problem they solve`). This is the extension point — narrow the list to keep cost down and word the prompt to ask whatever you want. One run is billed per row.

Runs are issued concurrently through a thread pool, with a rate limiter that keeps run *creation* under the Parallel limit of **2000/min** (result polling is free and doesn't count). Progress is checkpointed to `output/inc5000_2025_pr.jsonl` as each row finishes, so an interrupted run **resumes** instead of repeating completed (paid) research — delete the JSONL to force a clean re-run. The cheapest `lite` processor is used by default; tune the processor, concurrency, and rate knobs at the top of the file.
