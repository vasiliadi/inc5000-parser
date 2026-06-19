# inc5000 parser

![Python](https://img.shields.io/badge/Python-3.12-blue)

Single-file scraper ([parser.py](parser.py)) that extracts the Inc. 5000 (2025) company list from `https://www.inc.com/inc5000/2025` into a CSV. The site is a Next.js App Router SPA, so the scraper drives a headless Chromium browser via Playwright and reads the rendered DOM — there is no static HTML or JSON to fetch.

## Setup & Run

```bash
pip install playwright pandas
playwright install chromium   # one-time: downloads the Chromium binary
python parser.py
```

Output is written to `inc5000_2025.csv` (see `OUTPUT`). Tune `PAGES_TO_SCRAPE` at the top of the file to limit how many pages are clicked through.

There are no tests, no lint config, and no build step.

## Columns

RANK, COMPANY, 3-YEAR GROWTH, REVENUE RANGE, EMPLOYEE GROWTH, YEAR FOUNDED, INDUSTRY, CITY, STATE.

Revenue Range, Employee Growth, and Year Founded are paywalled — for non-subscribers these cells are blank / show a 🔒, so those CSV fields come out empty.
