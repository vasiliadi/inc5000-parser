# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See [README.md](README.md) for the overview, setup, and run commands. This file covers what's needed to *modify* the scraper.

## Workflow conventions

- **Always run Python through `uv`.** Use `uv run <script>` (e.g. `uv run main.py`) instead of bare `python`. Never invoke the interpreter directly.
- **Add dependencies with `uv add <package>`.** Don't hand-edit `pyproject.toml` to add or bump dependencies — let `uv` manage the manifest and `uv.lock`.
- **Before every commit, all three of these must pass:**

  ```bash
  uvx ruff format .
  uvx ruff check .
  uvx ty check .
  ```
  
- **Commit messages use the scope-prefixed format.** Lead with the area of the codebase that changed, not a change type — the description already conveys what kind of change it is, and the scope is what people actually scan for when debugging or reviewing history.

  ```text
  scope: description

  [optional body]
  ```

## Why firecrawl (don't try to "just hit the API", and don't use a local browser)

- It's a **Next.js App Router** app. The RSC "flight" payload embedded in the HTML is encrypted/obfuscated — it is not readable JSON, so you cannot regex the data out of the page source.
- **Pagination is pure client-side JavaScript.** Clicking page 2/3/4 fires *no* network requests. The entire dataset is loaded once and the UI just re-slices it in the DOM. There is no clean JSON API endpoint to target.
- **Driving a local browser (Playwright) gets blocked.** The site restricts automated browser traffic almost immediately — in practice you get cut off right after the first page. Use **`firecrawl-py`**, which renders the page server-side from rotating infrastructure, so extraction survives past page one.

## Architecture notes

The scraper uses `firecrawl-py` to render the page and return its content, then parses the table out of the result:

- Firecrawl handles the JavaScript rendering and any rate-limiting / anti-bot friction that broke the Playwright approach. Configure it with the `FIRECRAWL_API_KEY` environment variable.
- Because the full dataset is loaded client-side and the pager only re-slices the DOM, advancing through pages requires Firecrawl **actions** (click the next/page control) between scrapes, or scraping each page URL/state and stitching the rows together.
- Cell order is mapped positionally onto `HEADERS` — if Inc.'s column order changes, `HEADERS` must change to match.
- **Dedupe** on a `rank|company` key so re-reading the same page (e.g. if pagination silently fails to advance) won't duplicate rows.

When the table structure or pagination breaks, the fixes live in the parsing logic and the Firecrawl action/scrape configuration.

## Site-specific gotchas

- **Locked columns:** Revenue Range, Employee Growth, and Year Founded are paywalled — for non-subscribers these cells are blank / show a 🔒. Expect those CSV fields to come out empty; that's a paywall, not a scraper bug.
- **Sliding-window pagination:** the pager only shows ~5 numbered buttons (e.g. pages 1–5) at a time, so any "next page" logic must keep working as the window slides — don't assume a fixed full list of page buttons is present.
- **Rows-per-page:** a custom dropdown controls page size (default 10, also offers 50). Since the whole dataset is already loaded client-side, raising page size to 50 is the cheapest way to cut the number of pagination steps.
