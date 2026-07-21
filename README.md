# NYT Article Corpus

**Status: Work in progress.**

Pipeline for collecting New York Times articles via the Wayback Machine for [brief description of the research use case].

## Scripts
- `extract_urls.py` — extracts article URLs from [source]
- `fetch_wayback.py` — fetches archived article content from the Wayback Machine
- `migrate_to_per_year.py` — reorganizes fetched data by year
