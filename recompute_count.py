# python recompute_count.py
# Re-derives count.json's authoritative totals from the actual files on disk,
# so a race between concurrent/queued runs can never leave it permanently wrong
# (each run's own in-memory count is only a best-effort snapshot at save time).

import glob
import json
import os
from collections import defaultdict

by_year_totals = defaultdict(int)
for path in glob.glob("fetched/*/*.json"):
    year = path.split("/")[1]
    try:
        arts = json.load(open(path))
    except (FileNotFoundError, json.JSONDecodeError):
        continue
    by_year_totals[year] += len(arts)

total_fetched = sum(by_year_totals.values())

total_in_dataset = 0
for path in glob.glob("urls/*.txt"):
    with open(path) as fh:
        total_in_dataset += sum(1 for line in fh if line.strip())

count = {}
if os.path.exists("count.json"):
    try:
        count = json.load(open("count.json"))
    except json.JSONDecodeError:
        count = {}

count["total_in_dataset"] = total_in_dataset
count["total_fetched_all_runs"] = total_fetched
count["total_remaining"] = total_in_dataset - total_fetched
count["all_runs"] = {"by_year": {yr: {"fetched": n} for yr, n in sorted(by_year_totals.items())}}

tmp = "count.json.tmp"
with open(tmp, "w") as f:
    json.dump(count, f, indent=2)
os.replace(tmp, "count.json")

print(json.dumps({
    "total_in_dataset": total_in_dataset,
    "total_fetched_all_runs": total_fetched,
    "total_remaining": total_in_dataset - total_fetched,
}, indent=2))
