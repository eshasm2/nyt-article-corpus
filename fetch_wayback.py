# python fetch_wayback.py

import glob
import json
import os
import random
import re
import time
from collections import defaultdict

import requests
from bs4 import BeautifulSoup

PER_YEAR = 500
DELAY = 1.2  # seconds between requests
MAX_RUNTIME_SECONDS = 5.5 * 3600  # save progress before GHA's 6-hour hard limit

WAYBACK_AVAILABLE = "https://archive.org/wayback/available"
HEADERS = {"User-Agent": "Mozilla/5.0 (research project)"} # label  script sends when server makes HTTP request, otherwise looks like bot

# strip the paywall info
PAYWALL_RE = re.compile(
    r"(get unlimited access|subscribe to continue|already a subscriber|"
    r"create a free account|log in|sign in to continue|"
    r"all print options include free unlimited)",
    re.IGNORECASE,
)



def get_wayback_url(article_url, timestamp):
    try:
        r = requests.get( WAYBACK_AVAILABLE, params={"url": article_url, "timestamp": timestamp}, headers=HEADERS, timeout=10,)
        snap = r.json().get("archived_snapshots", {}).get("closest", {})
        if snap.get("available"):
            return snap["url"].replace("http://", "https://", 1)
    except Exception:
        pass
    return None


def extract_text(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "figure", "figcaption", "form", "noscript"]):
        tag.decompose()

    for sel in [
        "section[name='articleBody']",
        "div.StoryBodyCompanionColumn",
        "div.story-body",
        "article",
        "div#story",
        "div.entry-content",
        "div.post-content",
    ]:
        node = soup.select_one(sel)
        if node:
            text = node.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return clean(strip_paywall(text))

    paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if len(p.get_text(strip=True)) > 40]
    return clean(strip_paywall("\n".join(paragraphs)))


def strip_paywall(text):
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines[:8]):
        if PAYWALL_RE.search(line):
            start = i + 1
    return "\n".join(lines[start:])


def clean(text):
    try:
        text = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def collect_by_year():
    by_year = defaultdict(list)
    url_files = sorted(glob.glob("urls/[0-9][0-9][0-9][0-9].txt"))
    print(f"Loading {len(url_files)} URL files...")
    for path in url_files:
        year = os.path.splitext(os.path.basename(path))[0]
        with open(path) as fh:
            urls = [line.strip() for line in fh if line.strip()]
        by_year[year] = urls
    return by_year


def load_existing():
    try:
        with open("success.json") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def main():
    existing = load_existing()
    already_fetched = {a["article_url"] for a in existing}
    print(f"Already fetched: {len(already_fetched)} articles from previous runs")

    by_year = collect_by_year()
    years = sorted(by_year)
    print(f"Years found: {years}")

    sample = {}
    for year in years:
        pool = [u for u in by_year[year] if u not in already_fetched]
        sample[year] = random.sample(pool, min(PER_YEAR, len(pool)))

    total = sum(len(v) for v in sample.values())
    print(f"Total to fetch this run: {total} ({PER_YEAR} new per year)\n")

    success = list(existing)
    year_counts = {}
    run_start = time.time()

    fetched = 0
    timed_out = False
    for year in years:
        urls = sample[year]
        year_success = 0
        year_failed = 0
        timestamp = f"{year}0701"  # mid-year snapshot for each year

        for url in urls:
            if time.time() - run_start >= MAX_RUNTIME_SECONDS:
                print(f"\nTime limit reached — saving progress and exiting early.")
                timed_out = True
                break
            fetched += 1
            print(f"[{fetched}/{total}] {url}")
            wb_url = get_wayback_url(url, timestamp)

            if not wb_url:
                print(f"  -> no snapshot")
                year_failed += 1
                time.sleep(DELAY)
                continue

            text = None
            last_error = None
            for attempt in range(3):
                try:
                    r = requests.get(wb_url, headers=HEADERS, timeout=20)
                    r.raise_for_status()
                    text = extract_text(r.text)
                    break
                except Exception as e:
                    last_error = str(e)
                    print(f"  -> attempt {attempt + 1} failed: {last_error}")
                    time.sleep(DELAY * (attempt + 2))

            if text:
                success.append({"year": year, "article_url": url, "wayback_url": wb_url, "text": text})
                year_success += 1
            else:
                reason = "no_text_extracted" if last_error is None else f"fetch_error: {last_error}"
                print(f"  -> failed: {reason}")
                year_failed += 1

            time.sleep(DELAY)

        year_counts[year] = {"sampled": len(urls), "success": year_success, "failed": year_failed}
        print(f"  {year}: {year_success} success, {year_failed} failed\n")
        if timed_out:
            break

    total_in_dataset = sum(len(v) for v in by_year.values()) + len(already_fetched)
    new_success = len(success) - len(existing)

    all_runs_by_year = defaultdict(int)
    for a in success:
        year = a.get("year")
        if not year:
            m = re.search(r"nytimes\.com/(\d{4})/", a.get("article_url", ""))
            year = m.group(1) if m else "unknown"
        all_runs_by_year[year] += 1

    count = {
        "total_in_dataset": total_in_dataset,
        "total_fetched_all_runs": len(success),
        "total_remaining": total_in_dataset - len(success),
        "all_runs": {
            "by_year": {yr: {"fetched": all_runs_by_year[yr]} for yr in sorted(all_runs_by_year)},
        },
        "this_run": {
            "sampled": total,
            "success": new_success,
            "failed": total - new_success,
            "by_year": year_counts,
        },
    }

    with open("success.json", "w") as f:
        json.dump(success, f, indent=2)
    with open("count.json", "w") as f:
        json.dump(count, f, indent=2)

    print(f"Done. +{new_success} new articles ({len(success)} total across all runs).")
    print("success.json / count.json written.")


if __name__ == "__main__":
    main()
