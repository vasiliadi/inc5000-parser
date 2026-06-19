"""
Inc. 5000 (2025) parser — handles JS/SPA pagination by clicking through pages.
"""

import csv
import time

from playwright.sync_api import sync_playwright

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


def extract_rows(page):
    """Read the currently-rendered table rows out of the DOM."""
    return page.evaluate("""() => {
        const rows = [...document.querySelectorAll('table tbody tr')];
        return rows.map(tr =>
            [...tr.querySelectorAll('td')].map(td => td.innerText.trim())
        ).filter(cells => cells.length >= 8);
    }""")


def first_rank_on_page(page):
    rows = extract_rows(page)
    return rows[0][0] if rows else None


def goto_next_page(page):
    """Click the 'next page' arrow. Returns True if it advanced."""
    before = first_rank_on_page(page)
    # The next arrow is the chevron button after the numbered page buttons.
    # Fall back strategies make this resilient to minor markup changes.
    clicked = page.evaluate("""() => {
        // Prefer an explicit next/aria control if present
        let btn = document.querySelector('button[aria-label*="next" i], a[aria-label*="next" i]');
        if (!btn) {
            // Otherwise: the chevron button sitting right after the numeric page buttons
            const btns = [...document.querySelectorAll('button')];
            const nums = btns.filter(b => /^\\d+$/.test(b.innerText.trim()));
            if (nums.length) {
                const last = nums[nums.length - 1];
                // the element after the numeric group that is clickable
                let n = last.nextElementSibling;
                while (n && n.tagName !== 'BUTTON' && !n.querySelector?.('button')) n = n.nextElementSibling;
                btn = n && (n.tagName === 'BUTTON' ? n : n.querySelector('button'));
            }
        }
        if (btn && !btn.disabled) { btn.click(); return true; }
        return false;
    }""")
    if not clicked:
        return False
    # wait until the table content actually changes
    for _ in range(40):
        time.sleep(0.15)
        if first_rank_on_page(page) != before:
            return True
    return False


def main():
    all_rows, seen = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60_000)
        page.wait_for_selector("table tbody tr", timeout=30_000)

        for i in range(PAGES_TO_SCRAPE):
            for cells in extract_rows(page):
                key = cells[0] + "|" + cells[1]  # rank|company dedupe
                if key not in seen:
                    seen.add(key)
                    all_rows.append(dict(zip(HEADERS, cells)))
            print(f"page {i + 1}: collected {len(all_rows)} rows so far")
            if i < PAGES_TO_SCRAPE - 1 and not goto_next_page(page):
                print("No further pages.")
                break

        browser.close()

    with open(OUTPUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS)
        w.writeheader()
        w.writerows(all_rows)
    print(f"Done: {len(all_rows)} rows -> {OUTPUT}")


if __name__ == "__main__":
    main()
