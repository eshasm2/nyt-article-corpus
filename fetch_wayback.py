# python fetch_wayback.py

import glob
import json
import os
import random
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

PER_YEAR = 250
WORKERS = 4   # parallel fetchers
DELAY = 1.2   # seconds each worker sleeps between requests
MAX_RUNTIME_SECONDS = 5.5 * 3600  # save progress before GHA's 6-hour hard limit

WAYBACK_AVAILABLE = "https://archive.org/wayback/available"
HEADERS = {"User-Agent": "Mozilla/5.0 (research project)"}

PAYWALL_RE = re.compile(
    r"(get unlimited access|subscribe to continue|already a subscriber|"
    r"create a free account|log in|sign in to continue|"
    r"all print options include free unlimited)",
    re.IGNORECASE,
)


def get_wayback_url(article_url, timestamp):
    try:
        r = requests.get(WAYBACK_AVAILABLE, params={"url": article_url, "timestamp": timestamp}, headers=HEADERS, timeout=10)
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


def fetch_one(url, year):
    """Fetch a single article. Returns a result dict. Called from worker threads."""
    m = re.search(r"/(\d{4}/\d{2}/\d{2})/", url)
    timestamp = m.group(1).replace("/", "") if m else f"{year}0701"

    wb_url = get_wayback_url(url, timestamp)
    if not wb_url:
        time.sleep(DELAY)
        return {"status": "no_snapshot", "url": url, "year": year}

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
            time.sleep(DELAY * (attempt + 2))

    time.sleep(DELAY)

    if text:
        return {"status": "success", "url": url, "year": year, "wb_url": wb_url, "text": text}
    reason = "no_text_extracted" if last_error is None else f"fetch_error: {last_error}"
    return {"status": reason, "url": url, "year": year}


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
    articles = []
    for path in sorted(glob.glob("fetched/*.json")):
        try:
            with open(path) as f:
                articles.extend(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return articles


def save_progress(success, by_year, existing, year_counts, total, run_label=""):
    new_success = len(success) - len(existing)

    os.makedirs("fetched", exist_ok=True)
    by_year_articles = defaultdict(list)
    for a in success:
        by_year_articles[a["year"]].append(a)
    for yr, articles in by_year_articles.items():
        path = f"fetched/{yr}.json"
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(articles, f, indent=2)
        os.replace(tmp, path)

    all_runs_by_year = {yr: {"fetched": len(arts)} for yr, arts in sorted(by_year_articles.items())}
    total_in_dataset = sum(len(v) for v in by_year.values()) + len(existing)
    count = {
        "total_in_dataset": total_in_dataset,
        "total_fetched_all_runs": len(success),
        "total_remaining": total_in_dataset - len(success),
        "all_runs": {"by_year": all_runs_by_year},
        "this_run": {
            "sampled": total,
            "success": new_success,
            "failed": total - new_success,
            "by_year": year_counts,
        },
    }
    tmp = "count.json.tmp"
    with open(tmp, "w") as f:
        json.dump(count, f, indent=2)
    os.replace(tmp, "count.json")

    label = f" ({run_label})" if run_label else ""
    print(f"  Saved{label}: {len(success)} total articles (+{new_success} this run).")


def main():
    existing = load_existing()
    already_fetched = {a["article_url"] for a in existing}
    print(f"Already fetched: {len(already_fetched)} articles from previous runs")

    by_year = collect_by_year()
    years = sorted(by_year)
    random.shuffle(years)
    print(f"Years found: {years}")

    sample = {}
    for year in years:
        pool = [u for u in by_year[year] if u not in already_fetched]
        sample[year] = random.sample(pool, min(PER_YEAR, len(pool)))

    total = sum(len(v) for v in sample.values())
    print(f"Total to fetch this run: {total} ({PER_YEAR} per year, {WORKERS} workers)\n")

    success = list(existing)
    year_counts = {}
    run_start = time.time()
    timed_out = False

    for year in years:
        if timed_out:
            break
        if time.time() - run_start >= MAX_RUNTIME_SECONDS:
            print("\nTime limit reached — saving progress and exiting early.")
            timed_out = True
            break

        urls = sample[year]
        year_success = 0
        year_failed = 0
        fetched_this_year = 0

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {executor.submit(fetch_one, url, year): url for url in urls}
            for future in as_completed(futures):
                result = future.result()
                fetched_this_year += 1
                print(f"  [{fetched_this_year}/{len(urls)}] {result['url'][:80]}")

                if result["status"] == "success":
                    success.append({
                        "year": result["year"],
                        "article_url": result["url"],
                        "wayback_url": result["wb_url"],
                        "text": result["text"],
                    })
                    year_success += 1
                else:
                    print(f"    -> {result['status']}")
                    year_failed += 1

                if time.time() - run_start >= MAX_RUNTIME_SECONDS:
                    print("\nTime limit reached mid-year — saving and stopping.")
                    timed_out = True
                    break

        year_counts[year] = {"sampled": len(urls), "success": year_success, "failed": year_failed}
        print(f"  {year}: {year_success} success, {year_failed} failed")
        save_progress(success, by_year, existing, year_counts, total, run_label=year)

    save_progress(success, by_year, existing, year_counts, total, run_label="final")
    print("Done.")


if __name__ == "__main__":
    main()
