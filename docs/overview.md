# Project Overview

## Purpose

This project collects, normalises, and analyses tablet pricing data from **11 Azerbaijani online retail and marketplace platforms**. The goal is to produce actionable business intelligence about the local tablet market — covering price positioning, discount behaviour, financing availability, and secondary-market dynamics.

The pipeline runs end-to-end from raw scraping through to annotated charts and an executive-level report.

---

## Project Structure

```
tablet_price_analyse/
│
├── scripts/                  Python scripts (scrapers + analysis)
│   ├── irshad.py             Scraper — irshad.az
│   ├── kontakt.py            Scraper — kontakt.az
│   ├── smartelectronics.py   Scraper — smartelectronics.az
│   ├── bakuelectronics.py    Scraper — bakuelectronics.az
│   ├── mgstore.py            Scraper — mgstore.az
│   ├── birmarket.py          Scraper — birmarket.az
│   ├── tapaz.py              Scraper — tap.az (GraphQL)
│   ├── wtaz.py               Scraper — w-t.az
│   ├── soliton.py            Scraper — soliton.az
│   ├── bytelecom.py          Scraper — bytelecom.az
│   ├── texnohome.py          Scraper — texnohome.az
│   ├── combine.py            Merge all CSVs → data/data.csv
│   └── generate_charts.py    Produce all business charts
│
├── data/                     Raw + combined data
│   ├── irshad.csv            Per-source output (one file per scraper)
│   ├── kontakt.csv
│   ├── ...
│   └── data.csv              Combined, normalised master dataset
│
├── charts/                   PNG chart outputs
│   ├── catalogue_size.png
│   ├── median_price_retail.png
│   └── ...  (9 charts total)
│
├── docs/                     This documentation directory
│   ├── overview.md           ← you are here
│   ├── data_sources.md       Per-scraper technical reference
│   ├── data_dictionary.md    Field definitions for all CSVs
│   ├── setup_and_usage.md    Installation and run instructions
│   └── analysis_methodology.md  How insights and charts were produced
│
├── prompts/                  Source prompt files
│   └── analyse.txt
│
└── README.md                 Executive business report (non-technical)
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────┐
│  11 × scraper scripts                                   │
│  (scripts/*.py)                                         │
│                                                         │
│  Each fetches its target site and writes a              │
│  site-specific CSV to data/<source>.csv                 │
└───────────────────────┬─────────────────────────────────┘
                        │  11 CSV files
                        ▼
┌─────────────────────────────────────────────────────────┐
│  scripts/combine.py                                     │
│                                                         │
│  • Renames columns to a common schema                   │
│  • Adds `source` column to every row                    │
│  • Writes data/data.csv  (3,559 rows, 33 columns)       │
└───────────────────────┬─────────────────────────────────┘
                        │  data/data.csv
                        ▼
┌─────────────────────────────────────────────────────────┐
│  scripts/generate_charts.py                             │
│                                                         │
│  • Reads data/data.csv                                  │
│  • Produces 9 PNG charts in charts/                     │
└───────────────────────┬─────────────────────────────────┘
                        │  charts/*.png
                        ▼
                  README.md  (business report)
```

---

## Platforms Covered

| Platform | URL | Type | Listings |
|----------|-----|------|----------|
| tap.az | tap.az | Marketplace (C2C/B2C) | 2,577 |
| birmarket.az | birmarket.az | Marketplace | 582 |
| smartelectronics.az | smartelectronics.az | Retail store | 80 |
| bakuelectronics.az | bakuelectronics.az | Retail store | 55 |
| kontakt.az | kontakt.az | Retail chain | 55 |
| mgstore.az | mgstore.az | Retail store | 54 |
| soliton.az | soliton.az | Retail store | 53 |
| irshad.az | irshad.az | Retail store | 48 |
| bytelecom.az | bytelecom.az | Retail store | 26 |
| texnohome.az | texnohome.az | Retail store | 21 |
| w-t.az | w-t.az | Retail store | 8 |

---

## Technology Stack

| Component | Library | Purpose |
|-----------|---------|---------|
| HTTP (primary) | `aiohttp` + `asyncio` | Async fetching with concurrency control |
| HTTP (fallback) | `curl_cffi` | Chrome TLS impersonation for Cloudflare-protected sites |
| HTML parsing | `beautifulsoup4` | Product card extraction |
| JSON parsing | stdlib `json` | API/GraphQL response parsing |
| Data output | stdlib `csv` | DictWriter for all CSVs |
| Charting | `matplotlib` + `numpy` | Business chart generation |

---

## Scraping Architecture Pattern

Every scraper follows the same pattern:

1. **Primary path** — `aiohttp.ClientSession` with a short `ClientTimeout(total=15, connect=8)` to fail fast if Cloudflare drops the connection silently.
2. **Fallback path** — `curl_cffi.requests.AsyncSession(impersonate="chrome124")` which spoofs a real Chrome TLS fingerprint to bypass Cloudflare challenges.
3. **Concurrency** — `asyncio.Semaphore(CONCURRENCY=3)` limits parallel requests; a `DELAY=1.0` second sleep is inserted per request to be respectful of server load.
4. **Deduplication** — each scraper deduplicates by `product_id` (falling back to `url`) before writing the CSV.

See `docs/data_sources.md` for per-site deviations from this pattern.
