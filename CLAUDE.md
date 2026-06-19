# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

See [README.md](README.md) for the overview, setup, and run commands. This file covers what's needed to *modify* the scraper.

## Why a browser is required (don't try to "just hit the API")

- It's a **Next.js App Router** app. The RSC "flight" payload embedded in the HTML is encrypted/obfuscated — it is not readable JSON, so you cannot regex the data out of the page source.
- **Pagination is pure client-side JavaScript.** Clicking page 2/3/4 fires *no* network requests. The entire dataset is loaded once and the UI just re-slices it in the DOM. There is no clean JSON API endpoint to target.
- Therefore the only reliable extraction path is rendering the page and reading `table tbody tr` out of the live DOM — which is what the scraper does.

## Architecture notes

The scraping loop is deliberately resilient to the site's markup, since selectors are the most fragile part of any scraper here:

- **`extract_rows()`** reads `table tbody tr` straight from the live DOM via `page.evaluate`, keeping only rows with ≥8 `<td>` cells. Cell order is mapped positionally onto `HEADERS` — if Inc.'s column order changes, `HEADERS` must change to match.
- **`goto_next_page()`** has a two-tier strategy: first try an explicit `aria-label*="next"` control, then fall back to finding the chevron button immediately after the numeric page buttons. Pagination success is confirmed by polling `first_rank_on_page()` until the first row's rank changes (not by waiting on a network event), because the SPA swaps table content in place.
- **Dedupe** uses a `rank|company` key in `seen`, so re-reading the same page (e.g. if pagination silently fails to advance) won't duplicate rows.

When the table structure or pagination breaks, the fixes almost always live in the inline `page.evaluate` JS strings in these two functions.

## Site-specific gotchas

- **Locked columns:** Revenue Range, Employee Growth, and Year Founded are paywalled — for non-subscribers these cells are blank / show a 🔒. Expect those CSV fields to come out empty; that's a paywall, not a scraper bug.
- **Sliding-window pagination:** the pager only shows ~5 numbered buttons (e.g. pages 1–5) at a time, so the "chevron after the last numeric button" fallback in `goto_next_page()` must keep working as the window slides — don't assume a fixed full list of page buttons is present.
- **Rows-per-page:** a custom dropdown controls page size (default 10, also offers 50). Since the whole dataset is already loaded client-side, raising page size to 50 is the cheapest way to cut the number of pagination clicks.
