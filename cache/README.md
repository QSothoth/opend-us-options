# OpenD US options — local cache directory

This directory holds the **local market-data cache** for `scripts/opend_us_options`.

- **Data files are gitignored.** Only this `README.md` and `.gitkeep` are tracked.
- Layout: `klines/<SYMBOL>/<KTYPE>.parquet` (or `.csv`) plus
  `<KTYPE>.fetched.json` (quota-protection manifest of already-fetched spans).

You can override the cache location with the `OPEND_US_OPTIONS_CACHE_DIR`
environment variable (useful for tests or a data disk).

## Regenerating the cache

```bash
python scripts/opend_us_options/fetch_klines.py \
    --start 2023-01-01 --end 2024-12-31 --execute
```

The warm-cache CLI only fetches ranges that are missing locally — already-cached
spans are never re-requested from OpenD (quota protection).

## Safe to delete

Everything under `klines/` is regenerable. Deleting it simply means the next
`fetch_klines.py --execute` run will re-fetch the missing ranges.
