"""
Inc. 5000 (2025) parser — renders the JS/SPA via firecrawl-py and walks its
client-side pagination inside a single browser action.

See CLAUDE.md for why firecrawl (and not a local browser) is required: the site
is a Next.js App Router SPA that blocks local headless browsers, the RSC payload
is encrypted, and pagination is pure client-side DOM re-slicing with no JSON API.

Why one big executeJavascript action instead of one action per page: firecrawl
caps a scrape at 50 actions and 60s of total `wait`-action time. Driving N pages
with a click + wait each blows past both caps. So all pagination runs *inside* a
single async script — its internal setTimeout sleeps don't count as wait actions,
and it's one action regardless of page count. firecrawl awaits the returned
promise and serializes the resolved value.
"""

import csv
import json

from firecrawl import Firecrawl

# Number of pages to walk. The script bumps the pager to 50 rows/page, so each
# page is ~50 companies (20 -> ~1000). Keep this modest: the whole walk runs
# inside ONE firecrawl executeJavascript action, which firecrawl kills after
# ~40-50s, so very large counts (e.g. 100) return nothing. ~50 pages is the
# practical ceiling per call.
URL = "https://www.inc.com/inc5000/2025"
PAGES_TO_SCRAPE = 20
OUTPUT = "inc5000_2025.csv"

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

# Single async script that walks the whole pager and returns every row.
# `__PAGES__` is substituted with PAGES_TO_SCRAPE before sending. The DOM logic
# (row reading, the resilient next-chevron finder, and the "wait until the first
# rank changes" advance check) is ported straight from the old Playwright
# scraper; dedupe runs here too so a pager that silently fails to advance can't
# duplicate rows. firecrawl evaluates the script as an expression, so it's an
# IIFE — and async, so internal sleeps replace Playwright's blocking waits.
SCRAPE_ALL_JS = """(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const PAGES = __PAGES__;

    const readRows = () =>
        [...document.querySelectorAll('table tbody tr')].map(tr =>
            [...tr.querySelectorAll('td')].map(td => td.innerText.trim())
        ).filter(cells => cells.length >= 8);
    // Cheap: read just the first row's rank cell (the advance loop polls this a
    // lot, so avoid rebuilding the whole 50-row array each tick).
    const firstRank = () => {
        const td = document.querySelector('table tbody tr td');
        return td ? td.innerText.trim() : null;
    };
    // Rows hydrate after their <tr> exists, so the cells are briefly blank. Poll
    // until the first rank is populated before trusting what we read.
    const waitForData = async () => {
        for (let i = 0; i < 40; i++) { if (firstRank()) return true; await sleep(150); }
        return false;
    };
    // React reconciles a new page cell-by-cell, so the top row can settle while
    // lower rows still show stale/half-updated values. Wait until two consecutive
    // reads match so we never capture a half-rendered page (e.g. duplicate ranks).
    const snapshot = () => JSON.stringify(readRows());
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

    await waitForData();  // first page hydrated
    await waitStable();   // ...and fully rendered

    // Best-effort: dismiss a cookie/consent banner if one is covering the page.
    const consent = [...document.querySelectorAll('button, a')].find(
        el => /accept|agree|got it/i.test(el.innerText || '')
    );
    if (consent) { consent.click(); await sleep(300); }

    // Bump the rows-per-page control to 50 to cut the number of advances. It's a
    // Radix UI Select (role="combobox"); open it and pick the "50 Rows" option.
    // Best-effort — if the markup changes we just paginate in steps of 10.
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
            for (let i = 0; i < 40; i++) { await sleep(150); if (readRows().length > 10) break; }
            await waitStable();
        }
    }

    const out = [], seen = new Set();
    for (let p = 0; p < PAGES; p++) {
        const before = firstRank();
        for (const cells of readRows()) {
            const key = cells[0] + '|' + cells[1];  // rank|company dedupe
            if (!seen.has(key)) { seen.add(key); out.push(cells); }
        }
        if (p === PAGES - 1 || !clickNext()) break;
        // Wait until the next page has rendered with populated cells — i.e. the
        // first rank is non-blank AND different from the page we just read.
        let changed = false;
        for (let i = 0; i < 60; i++) {
            await sleep(100);
            const fr = firstRank();
            if (fr && fr !== before) { changed = true; break; }
        }
        if (!changed) break;  // pager didn't advance — stop rather than spin
        await waitStable();   // let the rest of the rows finish reconciling
    }
    return { rows: out };
})()"""


def build_actions():
    """Two actions: wait for the table to render, then run the paginating script
    (kept well under firecrawl's 50-action / 60s-wait caps)."""
    return [
        {"type": "wait", "selector": "table tbody tr"},
        {
            "type": "executeJavascript",
            "script": SCRAPE_ALL_JS.replace("__PAGES__", str(PAGES_TO_SCRAPE)),
        },
    ]


def iter_page_rows(doc):
    """Yield the row arrays out of the executeJavascript action return.

    Entries are {"type": ..., "value": <JS return>}; our script returns
    {"rows": [...]}. Read defensively — the API may JSON-stringify the value."""
    actions = doc.actions or {}
    for entry in actions.get("javascriptReturns") or []:
        value = entry.get("value", entry) if isinstance(entry, dict) else entry
        if isinstance(value, str):  # API may JSON-stringify the return
            value = json.loads(value)
        if isinstance(value, dict):  # our {"rows": [...]} wrapper
            value = value.get("rows", [])
        if isinstance(value, list):  # skip any non-row returns
            yield from value


def main():
    client = Firecrawl()  # reads FIRECRAWL_API_KEY from the environment
    doc = client.scrape(
        URL,
        formats=["html"],  # just to satisfy a content format; unused below
        actions=build_actions(),
        timeout=300_000,  # generous: the paginating script runs server-side
    )

    all_rows, seen = [], set()
    for cells in iter_page_rows(doc):
        if len(cells) < 8:
            continue
        key = cells[0] + "|" + cells[1]  # rank|company dedupe (belt-and-suspenders)
        if key not in seen:
            seen.add(key)
            all_rows.append(dict(zip(HEADERS, cells)))

    if not all_rows:
        # Nothing came back — surface the action result keys so the cause (e.g. a
        # renamed result field or a consent overlay) is debuggable.
        print(
            f"No rows extracted. doc.actions keys: {list((doc.actions or {}).keys())}"
        )
        return

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Done: {len(all_rows)} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
