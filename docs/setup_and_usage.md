# Setup and Usage

## Requirements

- Python **3.10+** (uses `float | None` union type hint syntax)
- pip packages listed below

## Installation

```bash
# 1. Clone or navigate into the project
cd tablet_price_analyse

# 2. (Recommended) Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

# 3. Install dependencies
pip install aiohttp beautifulsoup4 curl_cffi matplotlib numpy
```

### Required packages

| Package | Version tested | Role |
|---------|---------------|------|
| `aiohttp` | ≥ 3.9 | Primary async HTTP client |
| `beautifulsoup4` | ≥ 4.12 | HTML parsing |
| `curl_cffi` | ≥ 0.6 | Cloudflare-bypass fallback |
| `matplotlib` | ≥ 3.8 | Chart generation |
| `numpy` | ≥ 1.26 | Numeric operations in charts |

`lxml` can optionally be installed as a faster HTML parser for BeautifulSoup (`bs4` will use it automatically if present):

```bash
pip install lxml
```

---

## Running a Single Scraper

Each scraper is a self-contained script. Run from the project root:

```bash
python3 scripts/irshad.py
python3 scripts/kontakt.py
python3 scripts/smartelectronics.py
python3 scripts/bakuelectronics.py
python3 scripts/mgstore.py
python3 scripts/birmarket.py
python3 scripts/tapaz.py
python3 scripts/wtaz.py
python3 scripts/soliton.py
python3 scripts/bytelecom.py
python3 scripts/texnohome.py
```

Each script:
- Creates `data/` if it does not exist
- Writes its output to `data/<source>.csv`
- Prints progress (page/batch/offset fetched, product count) to stdout

Typical runtime per scraper: **5–60 seconds** depending on catalogue size and site responsiveness.

> **Note:** tap.az has 2,577 listings across 72 cursor-based pages. Expect ~90 seconds for a full run.

---

## Running All Scrapers

No orchestrator script is included; run each individually or use a simple shell loop:

```bash
for script in scripts/irshad.py scripts/kontakt.py scripts/smartelectronics.py \
              scripts/bakuelectronics.py scripts/mgstore.py scripts/birmarket.py \
              scripts/tapaz.py scripts/wtaz.py scripts/soliton.py \
              scripts/bytelecom.py scripts/texnohome.py; do
    echo "Running $script …"
    python3 "$script"
done
```

---

## Combining All Sources

After all scrapers have run, merge the per-source CSVs into the master dataset:

```bash
python3 scripts/combine.py
```

Output: `data/data.csv` — 3,559 rows × 33 columns with a `source` column on every row.

The combine script is **idempotent**: re-running it overwrites `data/data.csv` with the latest per-source files.

---

## Generating Charts

```bash
python3 scripts/generate_charts.py
```

Output: 9 PNG files written to `charts/`.

| Chart file | What it shows |
|------------|---------------|
| `catalogue_size.png` | Listing count per platform |
| `median_price_retail.png` | Median price — retail stores only |
| `median_all_platforms.png` | Median price — all platforms |
| `price_segments.png` | Price-tier distribution per platform |
| `discount_depth.png` | Average and maximum discount % |
| `installment_coverage.png` | % of catalogue with financing |
| `tap_vs_retail_prices.png` | Price bucket comparison: tap.az vs retail |
| `samsung_tab_a9_range.png` | Price range for Samsung Tab A9 benchmark |
| `installment_terms.png` | Most popular installment durations |

---

## Full Pipeline (one-shot)

```bash
# 1. Scrape all sources
for script in scripts/irshad.py scripts/kontakt.py scripts/smartelectronics.py \
              scripts/bakuelectronics.py scripts/mgstore.py scripts/birmarket.py \
              scripts/tapaz.py scripts/wtaz.py scripts/soliton.py \
              scripts/bytelecom.py scripts/texnohome.py; do
    python3 "$script"
done

# 2. Combine
python3 scripts/combine.py

# 3. Generate charts
python3 scripts/generate_charts.py
```

---

## Troubleshooting

### Cloudflare 403 / timeout on aiohttp path

All scrapers automatically fall back to `curl_cffi` when `aiohttp` receives a 403 or times out. You will see a message like:

```
[403] Cloudflare — falling back to curl_cffi …
```

This is expected behaviour for sites with Cloudflare protection. No action is needed.

### `ModuleNotFoundError: No module named 'curl_cffi'`

```bash
pip install curl_cffi
```

### `JSONDecodeError` on soliton.az

If you see `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` on the aiohttp path, this is caused by `zstd` content encoding which aiohttp cannot decompress. The `Accept-Encoding` header in `scripts/soliton.py` is already set to exclude `zstd`; if the issue persists, the cffi fallback will handle it.

### `UnicodeDecodeError` on any scraper

The scrapers use `errors="replace"` when decoding raw bytes. If you still see decode errors, ensure `beautifulsoup4` and `lxml` are up to date:

```bash
pip install --upgrade beautifulsoup4 lxml
```

### Empty CSV / 0 products

1. Check internet connectivity.
2. Verify the target site is reachable in a browser.
3. The site may have changed its HTML structure — inspect the page and update the CSS selectors in the corresponding script.

---

## Re-running After Site Changes

If a site updates its HTML layout:

1. Open the relevant scraper in `scripts/`.
2. Read the docstring at the top — it documents the expected HTML selectors and API endpoints.
3. Update the selectors in `parse_products()` (or `parse_nodes()` for tap.az).
4. Re-run the scraper and verify the CSV output.
