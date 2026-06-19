"""
Inc. 5000 (2025) parser — renders the JS/SPA via firecrawl-py and walks its
client-side pagination with browser actions, all in a single scrape call.

See CLAUDE.md for why firecrawl (and not a local browser) is required: the site
is a Next.js App Router SPA that blocks local headless browsers, the RSC payload
is encrypted, and pagination is pure client-side DOM re-slicing with no JSON API.
"""

import csv
import json

from firecrawl import Firecrawl

URL = "https://www.inc.com/inc5000/2025"
PAGES_TO_SCRAPE = 100  # set how many pages you want
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

# Run once up front: dismiss a cookie/consent banner if present, then best-effort
# bump the custom rows-per-page dropdown to 50 to cut the number of click steps.
# The dropdown markup is unknown and fragile, so every step here is best-effort —
# if it fails, pagination still works, just in steps of 10.
#
# firecrawl evaluates the script as an *expression* (unlike Playwright's
# evaluate, it does not call a function for you), so the body is wrapped in an
# IIFE — otherwise the return value is the uninvoked function itself.
CONSENT_AND_PAGESIZE_JS = """(() => {
    // Dismiss a cookie/consent banner if one is covering the page.
    const consent = [...document.querySelectorAll('button, a')].find(
        el => /accept|agree|got it/i.test(el.innerText || '')
    );
    if (consent) consent.click();

    // Best-effort: open the rows-per-page control and choose 50.
    const opener = [...document.querySelectorAll('button, [role="button"]')].find(
        el => /^\\s*(10|per page|rows)/i.test(el.innerText || '')
    );
    if (opener) {
        opener.click();
        const opt = [...document.querySelectorAll('li, option, [role="option"], button, a')].find(
            el => /^\\s*50\\s*$/.test(el.innerText || '')
        );
        if (opt) opt.click();
    }
    return true;
})()"""

# Per page: read the currently-rendered rows, then best-effort advance to the
# next page. Reading happens BEFORE the click, so each call returns the correct
# current page and the click sets up the next call. The chevron finder mirrors
# the resilient logic from the old Playwright scraper. Wrapped in an IIFE so
# firecrawl invokes it (see CONSENT_AND_PAGESIZE_JS note).
EXTRACT_AND_NEXT_JS = """(() => {
    const rows = [...document.querySelectorAll('table tbody tr')].map(tr =>
        [...tr.querySelectorAll('td')].map(td => td.innerText.trim())
    ).filter(cells => cells.length >= 8);

    // Advance to the next page (best-effort; resilient to minor markup changes).
    let btn = document.querySelector('button[aria-label*="next" i], a[aria-label*="next" i]');
    if (!btn) {
        const btns = [...document.querySelectorAll('button')];
        const nums = btns.filter(b => /^\\d+$/.test(b.innerText.trim()));
        if (nums.length) {
            const last = nums[nums.length - 1];
            let n = last.nextElementSibling;
            while (n && n.tagName !== 'BUTTON' && !n.querySelector?.('button')) n = n.nextElementSibling;
            btn = n && (n.tagName === 'BUTTON' ? n : n.querySelector('button'));
        }
    }
    if (btn && !btn.disabled) btn.click();

    return { rows };
})()"""


def build_actions():
    """Assemble the single-call action sequence: wait, set page size, then for
    each page run extract-and-advance with a settle wait in between."""
    actions = [
        {"type": "wait", "selector": "table tbody tr"},  # table rendered
        {"type": "executeJavascript", "script": CONSENT_AND_PAGESIZE_JS},
        {"type": "wait", "milliseconds": 1500},  # let the re-slice settle
    ]
    for i in range(PAGES_TO_SCRAPE):
        actions.append({"type": "executeJavascript", "script": EXTRACT_AND_NEXT_JS})
        if i < PAGES_TO_SCRAPE - 1:
            actions.append({"type": "wait", "milliseconds": 1200})  # DOM updates
    return actions


def iter_page_rows(doc):
    """Yield each page's rows out of the executeJavascript action returns.

    Each entry is {"type": ..., "value": <JS return>}. The extract script returns
    {"rows": [...]}; the one-off consent/page-size script returns `true`, which we
    skip. Read defensively — the API may JSON-stringify the value."""
    actions = doc.actions or {}
    for entry in actions.get("javascriptReturns") or []:
        value = entry.get("value", entry) if isinstance(entry, dict) else entry
        if isinstance(value, str):  # API may JSON-stringify the return
            value = json.loads(value)
        if isinstance(value, dict):  # our {"rows": [...]} wrapper
            value = value.get("rows", [])
        if isinstance(value, list):  # skip non-row returns (e.g. consent `true`)
            yield from value


def main():
    client = Firecrawl()  # reads FIRECRAWL_API_KEY from the environment
    doc = client.scrape(
        URL,
        formats=["html"],  # just to satisfy a content format; unused below
        actions=build_actions(),
        timeout=180_000,  # generous: the whole action chain runs server-side
    )

    all_rows, seen = [], set()
    for cells in iter_page_rows(doc):
        if len(cells) < 8:
            continue
        key = cells[0] + "|" + cells[1]  # rank|company dedupe
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
