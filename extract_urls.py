# python extract_urls.py
# One-time script: reads the large year folders and writes urls/YYYY.txt
# (one URL per line) so GitHub Actions can run without the raw data.

import glob
import json
import os

OUTPUT_DIR = "urls"
os.makedirs(OUTPUT_DIR, exist_ok=True)

files = sorted(glob.glob("[0-9][0-9][0-9][0-9]/**/*.json", recursive=True))
print(f"Found {len(files)} source files")

by_year: dict[str, set[str]] = {}

for path in files:
    year = path.split("/")[0]
    try:
        with open(path) as fh:
            raw = json.load(fh)
        articles = raw if isinstance(raw, list) else raw.get("articles", [])
        for a in articles:
            if isinstance(a, dict) and a.get("article_url"):
                by_year.setdefault(year, set()).add(a["article_url"])
    except Exception as e:
        print(f"  skipping {path}: {e}")
        continue

for year, urls in sorted(by_year.items()):
    out = os.path.join(OUTPUT_DIR, f"{year}.txt")
    with open(out, "w") as fh:
        fh.write("\n".join(sorted(urls)) + "\n")
    print(f"  {year}: {len(urls):,} URLs -> {out}")

print("Done.")
