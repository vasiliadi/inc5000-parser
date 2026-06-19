"""
Inc. 5000 (2025) parser — scrapes the full list into a CSV using a firecrawl
persistent browser session.

See CLAUDE.md for why firecrawl (and not a local browser) is required: the site
is a Next.js App Router SPA that blocks local headless browsers, the RSC payload
is encrypted, and pagination is pure client-side DOM re-slicing with no JSON API.

Approach — one firecrawl browser session, driven in three phases:

  1. `browser()` opens a persistent Chromium session (a Playwright `page` global).
  2. NAV_JS navigates, waits for the data to hydrate, and bumps the pager to 50
     rows/page.
  3. WALK_JS runs the entire pagination *inside one page.evaluate* (no CDP
     round-trips, so it's fast) and returns the rows it collected.

The browser keeps its position between `execute` calls, so WALK_JS is run in a
loop: each call carries an in-browser time budget, returns what it collected
plus whether it reached the end, and the next call simply continues from the
page the browser is already on. This sidesteps the two hard limits that sink a
plain `scrape`: a single action is killed after ~45-50s, and stateless calls
would have to re-walk from page 1 every time. Rows are deduped on `rank|company`.
"""

import csv
import json
import os
import sys
import time

from firecrawl import Firecrawl

URL = "https://www.inc.com/inc5000/2025"
OUTPUT = "output/inc5000_2025.csv"

# The list is ~5000 companies = ~100 pages at 50/page. One execute() call can
# only return so much: firecrawl caps stdout near ~200KB, so each WALK call
# collects at most PAGES_PER_CALL pages (~1250 rows ≈ 110KB) and the driver loops
# — the browser keeps its position between calls, so successive calls continue
# where the last left off (no gaps, no re-walking). WALK_BUDGET_MS is a secondary
# guard that keeps a call under firecrawl's ~120s execution cap when the site is
# slow. SESSION_TTL must comfortably outlast the whole run.
PAGES_PER_CALL = 25
SESSION_TTL = 900
WALK_BUDGET_MS = 90_000

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

# Phase 2 (node). Navigate, wait for rows to hydrate (their <tr>s exist before
# the cells fill), then open the rows-per-page Radix Select and pick "50 Rows".
# `domcontentloaded` not `networkidle` — this ad-heavy site never goes idle.
NAV_JS = r"""
await (async () => {
const sleep = ms => new Promise(r => setTimeout(r, ms));
await page.goto('https://www.inc.com/inc5000/2025', { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(() => {});
try { await page.waitForSelector('table tbody tr td', { timeout: 60000 }); } catch (e) {}
let firstRank = '';
for (let i = 0; i < 90; i++) {
    firstRank = await page.evaluate(() => {
        const td = document.querySelector('table tbody tr td');
        return td ? td.innerText.trim() : '';
    });
    if (firstRank) break;
    await sleep(1000);
}
const set = await page.evaluate(async () => {
    const s = ms => new Promise(r => setTimeout(r, ms));
    const opener = [...document.querySelectorAll('[role=combobox], button')].find(
        e => /^\d+\s*Rows?$/i.test((e.innerText || '').trim())
    );
    if (!opener) return 'no-opener';
    opener.click();
    await s(500);
    const opt = [...document.querySelectorAll('[role=option], li, div, span')].find(
        e => /^50\s*Rows$/i.test((e.innerText || '').trim())
    );
    if (!opt) return 'no-option';
    opt.click();
    for (let i = 0; i < 40; i++) { await s(120); if (document.querySelectorAll('table tbody tr').length > 10) break; }
    return 'ok';
});
process.stdout.write(JSON.stringify({ firstRank, set }));
})();
"""

# Phase 3 (node). The whole walk runs inside one page.evaluate so all the DOM
# reads, stability checks, and "next" clicks happen in-page with no round-trips.
# It honours an in-browser time budget and reports whether it reached the end, so
# the driver can call it again to continue from the browser's current page.
# Placeholders __BUDGET_MS__ / __MAX_PAGES__ are substituted per call.
WALK_JS = r"""
await (async () => {
const result = await page.evaluate(async ({ budgetMs, maxPages }) => {
    const START = Date.now();
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const readRows = () =>
        [...document.querySelectorAll('table tbody tr')].map(tr =>
            [...tr.querySelectorAll('td')].map(td => td.innerText.trim())
        ).filter(cells => cells.length >= 8);
    const firstRank = () => {
        const td = document.querySelector('table tbody tr td');
        return td ? td.innerText.trim() : null;
    };
    const snapshot = () => JSON.stringify(readRows());
    // React reconciles a new page cell-by-cell; wait until two reads match so a
    // half-rendered page can't yield duplicate ranks.
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
                b => /^\d+$/.test(b.innerText.trim())
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

    const out = [];
    let reachedEnd = false;
    for (let p = 0; p < maxPages; p++) {
        await waitStable();
        for (const cells of readRows()) out.push(cells);  // driver dedupes
        if (Date.now() - START > budgetMs) break;          // resume next call
        const before = firstRank();
        if (!clickNext()) { reachedEnd = true; break; }     // no more pages
        let advanced = false;
        for (let i = 0; i < 80; i++) {
            await sleep(80);
            const fr = firstRank();
            if (fr && fr !== before) { advanced = true; break; }
        }
        if (!advanced) { reachedEnd = true; break; }
    }
    return { rows: out, reachedEnd, lastRank: firstRank() };
}, { budgetMs: __BUDGET_MS__, maxPages: __MAX_PAGES__ });
process.stdout.write(JSON.stringify(result));
})();
"""


def _is_rate_limit(exc):
    """True if `exc` looks like a firecrawl rate-limit (HTTP 429). Checked by
    status/message rather than a concrete exception type so we don't depend on
    firecrawl's internal exception module, which can move between releases."""
    return getattr(exc, "status_code", None) == 429 or "rate limit" in str(exc).lower()


def _create_session(client):
    """Open a browser session, backing off through firecrawl's create rate limit
    (a few sessions per minute on smaller plans)."""
    for attempt in range(6):
        try:
            return client.v2.browser(ttl=SESSION_TTL)
        except Exception as exc:  # noqa: BLE001 — re-raised below unless rate-limited
            if not _is_rate_limit(exc):
                raise
            print("  rate limited creating session; backing off…")
            time.sleep(10)
    raise RuntimeError("Could not create a browser session (rate limited).")


def _exec_json(client, session_id, code, timeout=110):
    """Run node code in the session and parse its JSON stdout (or return None)."""
    r = client.v2.browser_execute(session_id, code, language="node", timeout=timeout)
    if not r.success or not r.stdout:
        print(f"  execute issue: exit={r.exit_code} stderr={(r.stderr or '')[:200]}")
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        print(f"  non-JSON stdout: {r.stdout[:200]}")
        return None


def _navigate(client, session_id):
    """Navigate, hydrate, and set 50/page — retrying if the data fails to load."""
    for attempt in range(3):
        info = _exec_json(client, session_id, NAV_JS)
        if info and info.get("firstRank"):
            print(
                f"navigated: firstRank={info['firstRank']} pageSize={info.get('set')}"
            )
            return True
        print(f"  nav attempt {attempt + 1}/3 did not hydrate; retrying…")
        time.sleep(5)
    return False


def _walk_once(client, session_id):
    """One WALK_JS call. Returns `(rows, reached_end, last_rank)`, or None if the
    execute call failed (so the caller can stop)."""
    code = WALK_JS.replace("__BUDGET_MS__", str(WALK_BUDGET_MS)).replace(
        "__MAX_PAGES__", str(PAGES_PER_CALL)
    )
    data = _exec_json(client, session_id, code)
    if not data:
        return None
    rows = [cells for cells in (data.get("rows") or []) if len(cells) >= 8]
    return rows, bool(data.get("reachedEnd")), data.get("lastRank")


def _walk(client, session_id):
    """Call WALK_JS in a loop, continuing from the browser's current page each
    time, until it reports the end. Dedupe on rank|company."""
    by_key = {}
    for call in range(20):  # generous upper bound; normally ends far sooner
        result = _walk_once(client, session_id)
        if result is None:
            break
        rows, reached_end, last_rank = result
        added = 0
        for cells in rows:
            key = cells[0] + "|" + cells[1]
            if key not in by_key:
                by_key[key] = cells
                added += 1
        print(
            f"walk call {call + 1}: +{added} rows (total {len(by_key)}) "
            f"lastRank={last_rank} reachedEnd={reached_end}"
        )
        if reached_end or added == 0:
            break
    return list(by_key.values())


def main():
    client = Firecrawl()  # reads FIRECRAWL_API_KEY from the environment
    session = _create_session(client)
    print(f"session {session.id}")
    try:
        hydrated = _navigate(client, session.id)
        rows = _walk(client, session.id) if hydrated else []
    finally:
        client.v2.delete_browser(session.id)
        print("session closed")

    # Hard failures exit non-zero (via stderr) so automation can detect them.
    if not hydrated:
        sys.exit("Page never hydrated; aborting.")
    if not rows:
        sys.exit("No rows extracted.")

    # Ranks render with thousands separators ("1,000") and may carry decorations
    # like "#1,000" or "1,000*"; keep only the digits so they sort numerically.
    def rank_key(cells):
        digits = "".join(ch for ch in cells[0] if ch.isdigit())
        return int(digits) if digits else 1 << 30

    rows.sort(key=rank_key)
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADERS)
        w.writerows(rows)
    print(f"Done: {len(rows)} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
