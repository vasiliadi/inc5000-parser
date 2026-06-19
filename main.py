"""
Inc. 5000 (2025) parser — renders the JS/SPA via firecrawl-py and walks its
client-side pagination, stitching the full list together across several scrape
calls.

See CLAUDE.md for why firecrawl (and not a local browser) is required: the site
is a Next.js App Router SPA that blocks local headless browsers, the RSC payload
is encrypted, and pagination is pure client-side DOM re-slicing with no JSON API.

Why several calls: all paging runs inside firecrawl `executeJavascript` actions,
and firecrawl kills a single action after ~45-50s. At 50 rows/page that caps one
call at ~50 pages, but the full list is ~5000 companies = ~100 pages. So a driver
makes repeated calls: each call fast-forwards past the pages already collected,
then collects a window of new pages, until the whole list is covered. Rows are
deduped on `rank|company`, so overlapping windows and re-reads are harmless.

Each call's in-browser script is self-budgeting (stops well before firecrawl's
kill timer) and reports how far it actually got, so the driver resumes from the
real position even when the site is slow — it never assumes a fixed page landed.
"""

import csv
import time

from firecrawl import Firecrawl

URL = "https://www.inc.com/inc5000/2025"
OUTPUT = "inc5000_2025.csv"

# ~100 pages * 50 rows ≈ 5000 companies (the whole list). Lower it for a partial
# pull. WINDOW is how many pages one call collects before the driver starts the
# next call; smaller windows survive high site latency but cost more calls.
TOTAL_PAGES = 100
WINDOW = 18
OVERLAP = 2  # re-collect a couple of pages each call so boundaries can't gap

HEADERS = [
    "rank",
    "company",
    "growth_3yr",
    "revenue_range",
    "employee_growth",
    "year_founded",
    "industry",
    "city",
    "state",
]

# One call's in-browser script. Placeholders are substituted per call:
#   __SKIP__      pages to fast-forward past (already collected) before collecting
#   __COLLECT__   pages to collect this call
#   __BUDGET_MS__ stop skipping/collecting once this much wall time has elapsed,
#                 so the action returns before firecrawl's ~45-50s kill timer
# firecrawl evaluates the script as an expression and awaits the promise, so it's
# an async IIFE; internal setTimeout sleeps replace Playwright's blocking waits.
PAGE_JS = """(async () => {
    const START = Date.now();
    const BUDGET = __BUDGET_MS__, SKIP = __SKIP__, COLLECT = __COLLECT__;
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const inBudget = () => Date.now() - START < BUDGET;

    const readRows = () =>
        [...document.querySelectorAll('table tbody tr')].map(tr =>
            [...tr.querySelectorAll('td')].map(td => td.innerText.trim())
        ).filter(cells => cells.length >= 8);
    // Cheap: just the first row's rank cell (polled a lot while advancing).
    const firstRank = () => {
        const td = document.querySelector('table tbody tr td');
        return td ? td.innerText.trim() : null;
    };
    const snapshot = () => JSON.stringify(readRows());

    // Rows hydrate after their <tr> exists, so cells are briefly blank.
    const waitForData = async () => {
        for (let i = 0; i < 60; i++) { if (firstRank()) return true; await sleep(150); }
        return false;
    };
    // React reconciles a new page cell-by-cell; wait until two reads match so we
    // never capture a half-rendered page (which would yield duplicate ranks).
    const waitStable = async () => {
        let prev = snapshot();
        for (let i = 0; i < 20; i++) {
            await sleep(120);
            const cur = snapshot();
            if (cur === prev) return;
            prev = cur;
        }
    };

    // Click the 'next page' arrow; resilient to minor markup changes.
    const clickNext = () => {
        let btn = document.querySelector('button[aria-label*="next" i], a[aria-label*="next" i]');
        if (!btn) {
            const nums = [...document.querySelectorAll('button')].filter(
                b => /^\\d+$/.test(b.innerText.trim())
            );
            if (nums.length) {
                const last = nums[nums.length - 1];
                let n = last.nextElementSibling;
                while (n && n.tagName !== 'BUTTON' && !n.querySelector?.('button')) n = n.nextElementSibling;
                btn = n && (n.tagName === 'BUTTON' ? n : n.querySelector('button'));
            }
        }
        if (btn && !btn.disabled) { btn.click(); return true; }
        return false;
    };
    // Advance one page; resolves true only once the first rank actually changes.
    const advance = async () => {
        const before = firstRank();
        if (!clickNext()) return false;
        for (let i = 0; i < 80; i++) {
            await sleep(80);
            const fr = firstRank();
            if (fr && fr !== before) return true;
        }
        return false;
    };

    if (!(await waitForData())) return { rows: [], skipped: 0, collected: 0, lastRank: null };
    await waitStable();

    // Best-effort: dismiss a cookie/consent banner if one is covering the page.
    const consent = [...document.querySelectorAll('button, a')].find(
        el => /accept|agree|got it/i.test(el.innerText || '')
    );
    if (consent) { consent.click(); await sleep(300); }

    // Bump the rows-per-page control to 50 (a Radix Select: open it, pick
    // "50 Rows"). Best-effort — if it fails we just paginate in steps of 10.
    const opener = [...document.querySelectorAll('[role="combobox"], button')].find(
        el => /^\\d+\\s*Rows?$/i.test((el.innerText || '').trim())
    );
    if (opener) {
        opener.click();
        await sleep(500);
        const opt = [...document.querySelectorAll('[role="option"], li, div, span')].find(
            el => /^50\\s*Rows$/i.test((el.innerText || '').trim())
        );
        if (opt) {
            opt.click();
            for (let i = 0; i < 40; i++) { await sleep(120); if (readRows().length > 10) break; }
            await waitStable();
        }
    }

    // Fast-forward past pages already collected (no reads, no stability waits).
    let skipped = 0;
    for (let p = 0; p < SKIP && inBudget(); p++) {
        if (!(await advance())) break;
        skipped++;
    }

    // Collect this window's pages.
    const out = [];
    let collected = 0;
    for (let p = 0; p < COLLECT && inBudget(); p++) {
        await waitStable();
        for (const cells of readRows()) out.push(cells);
        collected++;
        if (p === COLLECT - 1) break;
        if (!(await advance())) break;  // reached the last page
    }
    return { rows: out, skipped, collected, lastRank: firstRank() };
})()"""


def _client():
    return Firecrawl()  # reads FIRECRAWL_API_KEY from the environment


def scrape_window(client, skip, collect, budget_ms=38_000):
    """Run one scrape call that skips `skip` pages then collects `collect` pages.
    Returns the script's result dict: {rows, skipped, collected, lastRank}."""
    script = (
        PAGE_JS.replace("__SKIP__", str(skip))
        .replace("__COLLECT__", str(collect))
        .replace("__BUDGET_MS__", str(budget_ms))
    )
    doc = client.scrape(
        URL,
        formats=["html"],  # just to satisfy a content format; unused below
        actions=[
            {"type": "wait", "selector": "table tbody tr"},
            {"type": "executeJavascript", "script": script},
        ],
        # inc.com blocks automated traffic; "auto" starts on the basic proxy and
        # escalates to the stealth (anti-bot) proxy when a request is blocked.
        proxy="auto",
        timeout=300_000,
    )
    for entry in (doc.actions or {}).get("javascriptReturns") or []:
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, dict):
            return value
    return {}


def scrape_all(client):
    """Drive repeated windows until the whole list is covered. Dedupe on
    rank|company; resume each call from the page the previous one actually
    reached (resilient to slow renders), and retry a call that comes back empty."""
    by_key = {}
    start = 0
    while start < TOTAL_PAGES:
        info, rows = {}, []
        for attempt in range(3):  # empty render -> retry (likely throttling)
            info = scrape_window(client, start, WINDOW)
            rows = info.get("rows") or []
            if rows:
                break
            print(f"  window start={start}: empty result, retry {attempt + 1}/2")
            time.sleep(5 * (attempt + 1))  # back off so the proxy can rotate
        if not rows:
            print(f"No rows after retries at start={start}; stopping early.")
            break

        added = 0
        for cells in rows:
            if len(cells) < 8:
                continue
            key = cells[0] + "|" + cells[1]
            if key not in by_key:
                by_key[key] = cells
                added += 1

        skipped, collected = info.get("skipped", 0), info.get("collected", 0)
        reached = skipped + collected  # highest page index this call walked to
        print(
            f"window start={start}: skipped={skipped} collected={collected} "
            f"added={added} total={len(by_key)} lastRank={info.get('lastRank')}"
        )

        # Stop if we made no forward progress (budget/latency wall) or nothing new
        # came back (reached the end of the list).
        if reached <= start or added == 0:
            break
        start = max(start + 1, reached - OVERLAP)

    return list(by_key.values())


def main():
    client = _client()
    rows = scrape_all(client)
    if not rows:
        print("No rows extracted.")
        return

    # Ranks render with thousands separators ("1,000"); strip them to sort.
    def rank_key(cells):
        digits = cells[0].replace(",", "")
        return int(digits) if digits.isdigit() else 1 << 30

    rows.sort(key=rank_key)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(rows)
    print(f"Done: {len(rows)} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
