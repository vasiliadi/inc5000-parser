# inc5000 parser

![Python](https://img.shields.io/badge/Python-3.12-blue)

Single-file scraper that extracts the Inc. 5000 (2025) company list from `https://www.inc.com/inc5000/2025` into a CSV. The site is a Next.js App Router SPA with no static HTML or JSON to fetch, and it blocks local headless browsers almost immediately — so the scraper uses [`firecrawl-py`](https://github.com/firecrawl/firecrawl) to render the page server-side and read the table out of the result.

## Setup & Run

```bash
uv sync
export FIRECRAWL_API_KEY=fc-...   # get one at https://firecrawl.dev
uv run src/parser.py
```

Always run the scraper through `uv run`, never bare `python`. Add new dependencies with `uv add`, not by editing `pyproject.toml` by hand.

By default it scrapes the **entire** list (~5000 companies) into `inc5000_2025.csv` (see `OUTPUT`). It does this with a firecrawl **persistent browser session**: one session navigates once, bumps the pager to 50 rows/page, then walks the client-side pagination in batches (each firecrawl `execute` call has a stdout-size limit, so `PAGES_PER_CALL` caps how many pages one batch returns and the driver loops until the list ends). Rows are deduped on `rank|company`.

## Columns

RANK, COMPANY, 3-YEAR GROWTH, REVENUE RANGE, EMPLOYEE GROWTH, YEAR FOUNDED, INDUSTRY, CITY, STATE.

Revenue Range, Employee Growth, and Year Founded are paywalled — for non-subscribers these cells are blank / show a 🔒, so those CSV fields come out empty.
