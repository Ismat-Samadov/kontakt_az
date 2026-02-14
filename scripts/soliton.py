"""
soliton.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)

API: POST https://soliton.az/ajax-requests.php
     Content-Type: application/x-www-form-urlencoded
     X-Requested-With: XMLHttpRequest

Payload:
  action    = loadProducts
  sectionID = 67          (tablets)
  brandID   = 0           (all brands)
  offset    = 0, 15, 30 … (increments by limit)
  limit     = 15
  sorting   = (empty)

Response: JSON  { html, hasMore, totalCount, loadedCount, availableFilters }
  html       → HTML fragment with .product-item cards
  totalCount → total products available  (used to pre-compute all offsets)
  hasMore    → True/False stop condition

Parallelisation: first batch gives totalCount → remaining offsets computed
and fetched concurrently.
"""

import asyncio
import csv
import math
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_CSV = DATA_DIR / "soliton.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL    = "https://soliton.az"
LISTING_URL = f"{BASE_URL}/catalog/planshetlar/"
AJAX_URL    = f"{BASE_URL}/ajax-requests.php"
SECTION_ID  = "67"     # tablets
LIMIT       = 15
CONCURRENCY = 3
DELAY       = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,ru;q=0.7,az;q=0.6",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Origin": BASE_URL,
    "Referer": LISTING_URL,
    "X-Requested-With": "XMLHttpRequest",
    "sec-ch-ua": '"Not(A:Brand";v="8","Chromium";v="144","Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
}

CSV_FIELDS = [
    "name",
    "product_id",
    "brand_id",
    "price_current",
    "price_old",
    "discount_pct",
    "discount_amount",
    "installment_6m",
    "installment_12m",
    "installment_18m",
    "in_stock",
    "special_offer",
    "category",
    "url",
    "image_url",
    "offset",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def clean_price(text: str) -> str:
    """'349.99 AZN' → '349.99'"""
    return re.sub(r"[^\d.]", "", text.replace(",", ".")).strip()


def parse_products(html: str, offset: int) -> list[dict]:
    """Parse all .product-item cards from one HTML fragment."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select(".product-item"):
        # ── data attributes ───────────────────────────────────────────
        product_id = ""
        cmp = card.select_one("span.icon.compare[data-item-id]")
        if cmp:
            product_id = cmp.get("data-item-id", "").strip()

        brand_id = card.get("data-brandid", "").strip()
        name     = card.get("data-title", "").strip()

        # ── URL & image ───────────────────────────────────────────────
        url = ""
        a_title = card.select_one("a.prodTitle[href], a.thumbHolder[href]")
        if a_title:
            href = a_title.get("href", "")
            url = href if href.startswith("http") else BASE_URL + href

        image_url = ""
        img = card.select_one(".pic img")
        if img:
            src = img.get("src", "")
            image_url = src if src.startswith("http") else BASE_URL + src

        # ── category ──────────────────────────────────────────────────
        category = ""
        cat_a = card.select_one("a.prodSection")
        if cat_a:
            category = cat_a.get_text(strip=True)

        # ── prices ────────────────────────────────────────────────────
        # .prodPrice: first span = current (cash), .creditPrice = old/credit
        price_current = card.get("data-price", "")
        price_old     = ""
        price_div = card.select_one(".prodPrice")
        if price_div:
            credit_el = price_div.select_one(".creditPrice")
            if credit_el:
                price_old = clean_price(credit_el.get_text())

        # ── discount ──────────────────────────────────────────────────
        discount_pct    = ""
        discount_amount = ""
        disc_pct_el = card.select_one(".saleStar .percent")
        disc_amt_el = card.select_one(".saleStar .moneydif .amount")
        if disc_pct_el:
            discount_pct = disc_pct_el.get_text(strip=True)
        if disc_amt_el:
            discount_amount = disc_amt_el.get_text(strip=True)

        # ── installment options ───────────────────────────────────────
        installments: dict[str, str] = {}
        for mp in card.select(".monthlyPayment[data-month]"):
            month = mp.get("data-month", "")
            amt_el = mp.select_one(".amount")
            if month and amt_el:
                installments[month] = amt_el.get_text(strip=True)

        # ── stock status ──────────────────────────────────────────────
        in_stock = "False" if card.select_one(".outofstock") else "True"

        # ── special offers ────────────────────────────────────────────
        offers = [
            el.get_text(strip=True)
            for el in card.select(".specialOffers .offer .label")
            if el.get_text(strip=True)
        ]
        special_offer = "; ".join(offers)

        products.append({
            "name":            name,
            "product_id":      product_id,
            "brand_id":        brand_id,
            "price_current":   price_current,
            "price_old":       price_old,
            "discount_pct":    discount_pct,
            "discount_amount": discount_amount,
            "installment_6m":  installments.get("6", ""),
            "installment_12m": installments.get("12", ""),
            "installment_18m": installments.get("18", ""),
            "in_stock":        in_stock,
            "special_offer":   special_offer,
            "category":        category,
            "url":             url,
            "image_url":       image_url,
            "offset":          offset,
        })

    return products


def build_payload(offset: int) -> dict:
    return {
        "action":    "loadProducts",
        "sectionID": SECTION_ID,
        "brandID":   "0",
        "offset":    str(offset),
        "limit":     str(LIMIT),
        "sorting":   "",
    }


# ── async fetch ──────────────────────────────────────────────────────────────

async def fetch_batch(
    session: aiohttp.ClientSession,
    offset: int,
    sem: asyncio.Semaphore,
) -> tuple[int, dict]:
    """POST one batch; return (offset, parsed_json)."""
    async with sem:
        await asyncio.sleep(DELAY)
        async with session.post(
            AJAX_URL,
            data=build_payload(offset),
            headers=HEADERS,
            ssl=False,
        ) as resp:
            resp.raise_for_status()
            raw = await resp.read()
            import json as _json
            data = _json.loads(raw.decode("utf-8", errors="replace"))
            print(f"  offset={offset:3d}  loaded={data.get('loadedCount')}  "
                  f"hasMore={data.get('hasMore')}")
            return offset, data


async def scrape_all() -> list[dict]:
    """Fetch batch 0 first, then all remaining offsets concurrently."""
    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    connector = aiohttp.TCPConnector(ssl=False)
    timeout   = aiohttp.ClientTimeout(total=20, connect=8)

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            # Prime session cookies
            async with session.get(
                LISTING_URL,
                headers={**HEADERS, "Accept": "text/html,*/*",
                         "Content-Type": "text/html"},
                ssl=False,
            ) as r:
                r.raise_for_status()

            # Batch 0: discover totalCount
            print("Fetching offset=0 …")
            _, data0 = await fetch_batch(session, 0, asyncio.Semaphore(1))
            prods0 = parse_products(data0["html"], 0)
            all_products.extend(prods0)

            total_count  = int(data0.get("totalCount", 0))
            loaded_count = int(data0.get("loadedCount", LIMIT))
            print(f"  totalCount={total_count}, first batch={loaded_count}")

            if not data0.get("hasMore") or total_count <= loaded_count:
                return all_products

            # Compute remaining offsets and fetch concurrently
            remaining_offsets = list(range(loaded_count, total_count, LIMIT))
            print(f"Fetching offsets {remaining_offsets} (concurrency={CONCURRENCY}) …")

            tasks = [fetch_batch(session, off, sem) for off in remaining_offsets]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in sorted(
                [r for r in results if not isinstance(r, Exception)],
                key=lambda x: x[0],
            ):
                off, data = result
                prods = parse_products(data["html"], off)
                all_products.extend(prods)
            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] {result}")

    except (aiohttp.ClientResponseError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
        status = getattr(e, "status", None)
        if status == 403 or status is None:
            reason = f"[{status}]" if status else "[timeout/connection error]"
            print(f"\n  {reason} Cloudflare — falling back to curl_cffi …\n")
            return await scrape_all_cffi()
        raise

    return all_products


# ── curl_cffi fallback ────────────────────────────────────────────────────────

async def scrape_all_cffi() -> list[dict]:
    """Fallback: curl_cffi async."""
    from curl_cffi.requests import AsyncSession

    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    async def fetch_cffi(s, offset: int) -> tuple[int, dict]:
        async with sem:
            await asyncio.sleep(DELAY)
            resp = await s.post(AJAX_URL, data=build_payload(offset), headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
            print(f"  [cffi] offset={offset}  loaded={data.get('loadedCount')}")
            return offset, data

    async with AsyncSession(impersonate="chrome124") as s:
        await s.get(LISTING_URL, headers={**HEADERS, "Accept": "text/html,*/*"})

        _, data0 = await fetch_cffi(s, 0)
        all_products.extend(parse_products(data0["html"], 0))
        total_count  = int(data0.get("totalCount", 0))
        loaded_count = int(data0.get("loadedCount", LIMIT))
        print(f"  totalCount={total_count}, first batch={loaded_count}")

        if data0.get("hasMore") and total_count > loaded_count:
            remaining = list(range(loaded_count, total_count, LIMIT))
            tasks   = [fetch_cffi(s, off) for off in remaining]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in sorted(
                [r for r in results if not isinstance(r, Exception)],
                key=lambda x: x[0],
            ):
                off, data = result
                all_products.extend(parse_products(data["html"], off))
            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] {result}")

    return all_products


# ── CSV writer ────────────────────────────────────────────────────────────────

def save_csv(products: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(products)
    print(f"\nSaved {len(products)} products → {path}")


# ── entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"Scraping: {LISTING_URL}")
    products = await scrape_all()

    if not products:
        print("No products found — check selectors or connectivity.")
        return

    # Deduplicate by product_id (falling back to url)
    seen: set[str] = set()
    unique = []
    for p in products:
        key = p["product_id"] or p["url"]
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
        elif not key:
            unique.append(p)

    print(f"\nTotal unique products: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    asyncio.run(main())
