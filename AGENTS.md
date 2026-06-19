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

The scraper drives a firecrawl **persistent browser session** (not a plain `scrape`). Configure firecrawl with the `FIRECRAWL_API_KEY` environment variable. `main.py` runs three phases over a single session (`client.v2.browser()` → `browser_execute()` → `delete_browser()`):

1. **Create** one browser session — it exposes a Playwright `page` global to executed code.
2. **`NAV_JS`** — `page.goto` the list, wait for the table to hydrate, and bump the pager to 50 rows/page.
3. **`WALK_JS`** — runs the entire pagination *inside one `page.evaluate`* (all DOM reads + "next" clicks happen in-page, so there are no slow CDP round-trips), and returns the rows it collected. The driver calls it in a loop; the browser keeps its position between calls, so each call continues from where the last left off.

Why a session and not a plain `scrape`: a single firecrawl action is killed after ~45–50s (so one `executeJavascript` can't walk all ~100 pages), and stateless `scrape` calls would have to re-walk from page 1 every time. A persistent session walks the whole list continuously.

- Cell order is mapped positionally onto `HEADERS` — if Inc.'s column order changes, `HEADERS` must change to match.
- **Dedupe** on a `rank|company` key so re-reads / boundary overlap between `WALK_JS` calls can't duplicate rows.
- Knobs live at the top of `main.py`: `PAGES_PER_CALL` (pages per `execute`, capped by stdout size), `WALK_BUDGET_MS` (per-call in-browser time budget), `SESSION_TTL`.

When the table structure or pagination breaks, the fixes live in `NAV_JS` / `WALK_JS` (the in-page DOM logic) and the driver loop.

## firecrawl browser-session gotchas (what bit us — read before editing the JS)

- **`page.goto` must use `domcontentloaded`, never `networkidle`.** Ad/analytics beacons keep the network busy, so `networkidle` never fires and just burns the timeout. After `goto`, poll until the first rank cell is non-empty (rows hydrate *after* their `<tr>` exists, so cells are briefly blank).
- **Read pages only once stable.** React reconciles a new page cell-by-cell; a too-early read yields a half-updated row (e.g. a duplicate rank). Wait until two consecutive reads match before collecting.
- **`execute` output capture (node):** `process.stdout.write(...)` → `stdout`; the last expression → `result`. `console.log` is **swallowed** — don't rely on it.
- **`execute` reuses one node scope across calls.** Top-level `const`/`let` persist, so a second call re-running the same code throws "Identifier already declared". Wrap each script body in `await (async () => { ... })();`.
- **stdout is capped (~200KB).** Returning all ~5000 rows in one call truncates the JSON. `PAGES_PER_CALL` keeps each call's payload small; the driver loops.
- **`execute` has a ~120s ceiling**, independent of the timeout you pass. `WALK_BUDGET_MS` keeps a call under it; if a call hits the budget it returns early and the next call resumes.
- **Session creation is rate-limited** (~3/min on smaller plans). `_create_session` backs off and retries.

## Site-specific gotchas

- **Locked columns:** Revenue Range, Employee Growth, and Year Founded are paywalled — for non-subscribers these cells are blank / show a 🔒. Expect those CSV fields to come out empty; that's a paywall, not a scraper bug.
- **Sliding-window pagination:** the pager only shows ~5 numbered buttons (e.g. pages 1–5) at a time, so any "next page" logic must keep working as the window slides — don't assume a fixed full list of page buttons is present.
- **Rows-per-page:** a **Radix UI Select** (`role="combobox"`) controls page size (default 10, options up to "50 Rows"). `NAV_JS` opens it and picks "50 Rows" to cut the number of pagination steps. The options read `"50 Rows"`, not `"50"` — match on that.
- **Duplicate / skipped ranks are real Inc. data, not bugs.** Rank 105 is shared by two companies (a tie), and rank 3259 is absent — so the full list is 5000 companies spanning ranks 1–5000 with one dup and one gap. The `rank|company` dedupe correctly keeps both 105s; don't "fix" it by deduping on rank alone.
