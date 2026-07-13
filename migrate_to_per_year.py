# python migrate_to_per_year.py
# One-time script: splits success.json into fetched/YYYY.json per-year files.

import json
import os
import re
from collections import defaultdict

os.makedirs("fetched", exist_ok=True)

print("Loading success.json...")
with open("success.json") as f:
    articles = json.load(f)

by_year = defaultdict(list)
for a in articles:
    year = a.get("year")
    if not year:
        m = re.search(r"nytimes\.com/(\d{4})/", a.get("article_url", ""))
        year = m.group(1) if m else "unknown"
        a["year"] = year
    by_year[year].append(a)

for year, items in sorted(by_year.items()):
    path = f"fetched/{year}.json"
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, indent=2)
    os.replace(tmp, path)
    print(f"  {year}: {len(items):,} articles -> {path}")

print(f"Done. {len(articles):,} articles migrated.")
