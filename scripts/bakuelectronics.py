"""
bakuelectronics.az tablet scraper
Next.js site — uses the _next/data JSON API (no HTML parsing needed).

Strategy:
  1. Fetch the listing page HTML to extract the Next.js buildId and the
     first page of products embedded in <script id="__NEXT_DATA__">.
  2. Calculate total pages from   total / size  (size = 18 per page).
  3. Fetch remaining pages concurrently from the JSON endpoint:
       GET /_next/data/{buildId}/az/catalog/telefonlar-qadcetler/plansetler.json
           ?slug=telefonlar-qadcetler&slug=plansetler&page={N}

The buildId changes on every deployment, so it must be read fresh each run.
"""

import asyncio
import csv
import json
import math
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_CSV = DATA_DIR / "bakuelectronics.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL     = "https://www.bakuelectronics.az"
LISTING_URL  = f"{BASE_URL}/az/catalog/telefonlar-qadcetler/plansetler"
PRODUCT_BASE = f"{BASE_URL}/az/mehsullar"   # /{slug}
CONCURRENCY  = 3
DELAY        = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,ru;q=0.7,az;q=0.6",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Referer": LISTING_URL,
    "sec-ch-ua": '"Not(A:Brand";v="8","Chromium";v="144","Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
    "x-nextjs-data": "1",
}

CSV_FIELDS = [
    "name",
    "product_id",
    "sku",
    "price_current",
    "price_old",
    "discount_amount",
    "installment_monthly",
    "installment_term",
    "quantity",
    "review_count",
    "rating",
    "is_online",
    "campaign",
    "url",
    "image_url",
    "page",
]


# ── JSON → product dict ──────────────────────────────────────────────────────

def item_to_dict(item: dict, page_num: int) -> dict:
    """Map one API item to a flat CSV row."""
    slug = item.get("slug", "")
    url = f"{PRODUCT_BASE}/{slug}" if slug else ""

    per_month = item.get("perMonth") or {}
    installment_monthly = per_month.get("price", "")
    installment_term = f"{per_month.get('month', '')} ay" if per_month.get("month") else ""

    # Collect campaign widget titles as a semicolon-separated string
    campaign_widgets = item.get("campaign_widgets") or []
    campaign = "; ".join(w.get("title", "").strip() for w in campaign_widgets if w.get("title"))

    return {
        "name":               item.get("name", "").strip(),
        "product_id":         item.get("id", ""),
        "sku":                item.get("product_code", ""),
        "price_current":      item.get("discounted_price", "") or item.get("price", ""),
        "price_old":          item.get("price", ""),
        "discount_amount":    item.get("discount", ""),
        "installment_monthly": installment_monthly,
        "installment_term":   installment_term,
        "quantity":           item.get("quantity", ""),
        "review_count":       item.get("reviewCount", ""),
        "rating":             item.get("rate", ""),
        "is_online":          item.get("is_online", ""),
        "campaign":           campaign,
        "url":                url,
        "image_url":          item.get("image", ""),
        "page":               page_num,
    }


def parse_page(data: dict, page_num: int) -> list[dict]:
    """Extract product dicts from a _next/data JSON payload."""
    try:
        items = data["pageProps"]["products"]["products"]["items"]
    except (KeyError, TypeError):
        return []
    return [item_to_dict(item, page_num) for item in (items or [])]


# ── bootstrap — build ID + page 1 ────────────────────────────────────────────

def extract_build_id(html: str) -> str:
    """Read the Next.js buildId from the page HTML."""
    m = re.search(r'"buildId":"([^"]+)"', html)
    if not m:
        raise ValueError("buildId not found in HTML — site may have changed.")
    return m.group(1)


def extract_page1_from_html(html: str) -> tuple[list[dict], int, int]:
    """
    Parse __NEXT_DATA__ from the listing page HTML.
    Returns (products_page1, total, size).
    """
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return [], 0, 18

    nd = json.loads(m.group(1))
    try:
        inner = nd["props"]["pageProps"]["products"]["products"]
    except (KeyError, TypeError):
        return [], 0, 18

    items = inner.get("items") or []
    total = int(inner.get("total") or 0)
    size  = int(inner.get("size") or 18)
    products = [item_to_dict(item, 1) for item in items]
    return products, total, size


# ── async fetch ──────────────────────────────────────────────────────────────

def api_url(build_id: str) -> str:
    return (
        f"{BASE_URL}/_next/data/{build_id}"
        "/az/catalog/telefonlar-qadcetler/plansetler.json"
    )


async def fetch_page(
    session: aiohttp.ClientSession,
    build_id: str,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, dict]:
    """Fetch one JSON page; return (page_number, data_dict)."""
    params = [
        ("slug", "telefonlar-qadcetler"),
        ("slug", "plansetler"),
        ("page", page),
    ]
    async with sem:
        await asyncio.sleep(DELAY)
        async with session.get(
            api_url(build_id), params=params, headers={**HEADERS, "Accept": "*/*"}, ssl=False
        ) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
            print(f"  Fetched page {page}  [status {resp.status}]")
            return page, data


async def scrape_all() -> list[dict]:
    """Orchestrate scraping all pages."""
    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    connector = aiohttp.TCPConnector(ssl=False)
    timeout   = aiohttp.ClientTimeout(total=15, connect=8)

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            # ── step 1: fetch listing HTML → build_id + page 1 ──────────
            print("Fetching listing page (build ID + page 1) …")
            async with session.get(
                LISTING_URL,
                headers={**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
                ssl=False,
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()

            build_id = extract_build_id(html)
            print(f"  Build ID: {build_id}")

            prods1, total, size = extract_page1_from_html(html)
            all_products.extend(prods1)
            print(f"  Page 1: {len(prods1)} products  (total={total}, size={size})")

            if total == 0 or size == 0:
                return all_products

            last_page = math.ceil(total / size)
            if last_page < 2:
                return all_products

            # ── step 2: fetch pages 2..last_page concurrently ────────────
            print(f"Fetching pages 2–{last_page} (concurrency={CONCURRENCY}) …")
            tasks = [
                fetch_page(session, build_id, p, sem)
                for p in range(2, last_page + 1)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] page fetch error: {result}")
                    continue
                page_num, data = result
                prods = parse_page(data, page_num)
                all_products.extend(prods)
                print(f"  Page {page_num}: {len(prods)} products")

    except (aiohttp.ClientResponseError, aiohttp.ServerTimeoutError, asyncio.TimeoutError) as e:
        status = getattr(e, "status", None)
        if status == 403 or status is None:
            reason = f"[{status}]" if status else "[timeout/connection error]"
            print(
                f"\n  {reason} Cloudflare blocked aiohttp — "
                "falling back to curl_cffi …\n"
            )
            return await scrape_all_cffi()
        raise

    return all_products


# ── curl_cffi fallback (Chrome TLS impersonation) ────────────────────────────

async def scrape_all_cffi() -> list[dict]:
    """Fallback: use curl_cffi async to bypass Cloudflare."""
    from curl_cffi.requests import AsyncSession

    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    async def fetch_cffi(s, build_id: str, page: int) -> tuple[int, dict]:
        params = [
            ("slug", "telefonlar-qadcetler"),
            ("slug", "plansetler"),
            ("page", page),
        ]
        async with sem:
            await asyncio.sleep(DELAY)
            resp = await s.get(
                api_url(build_id), params=params,
                headers={**HEADERS, "Accept": "*/*"},
            )
            resp.raise_for_status()
            print(f"  [cffi] page {page}  [{resp.status_code}]")
            return page, resp.json()

    async with AsyncSession(impersonate="chrome124") as s:
        # Fetch listing page for build_id + page 1
        print("Fetching listing page (cffi) …")
        r = await s.get(
            LISTING_URL,
            headers={**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )
        build_id = extract_build_id(r.text)
        print(f"  Build ID: {build_id}")

        prods1, total, size = extract_page1_from_html(r.text)
        all_products.extend(prods1)
        print(f"  Page 1: {len(prods1)} products  (total={total}, size={size})")

        if total == 0 or size == 0:
            return all_products

        last_page = math.ceil(total / size)
        if last_page < 2:
            return all_products

        print(f"Fetching pages 2–{last_page} (cffi) …")
        tasks = [fetch_cffi(s, build_id, p) for p in range(2, last_page + 1)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                print(f"  [warn] {result}")
                continue
            page_num, data = result
            prods = parse_page(data, page_num)
            all_products.extend(prods)
            print(f"  Page {page_num}: {len(prods)} products")

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
        print("No products found — check connectivity or selectors.")
        return

    # Deduplicate by product_id (falling back to url)
    seen: set[str] = set()
    unique = []
    for p in products:
        key = str(p["product_id"]) if p["product_id"] != "" else p["url"]
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
        elif not key:
            unique.append(p)

    print(f"\nTotal unique products: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    asyncio.run(main())
