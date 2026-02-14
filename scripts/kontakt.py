"""
Kontakt.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)
Scrapes all tablet listings and saves to data/kontakt.csv
"""

import asyncio
import csv
import json
import os
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_CSV = DATA_DIR / "kontakt.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL = "https://kontakt.az"
CATEGORY_URL = f"{BASE_URL}/plansetler-ve-elektron-kitablar/plansetler"
CONCURRENCY = 3          # simultaneous page fetches
DELAY = 1.0              # seconds between requests (per worker)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "az,en-US;q=0.9,en;q=0.8,ru;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "Referer": BASE_URL + "/",
    "sec-ch-ua": '"Chromium";v="124","Google Chrome";v="124"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Upgrade-Insecure-Requests": "1",
}

CSV_FIELDS = [
    "name",
    "brand",
    "sku",
    "price_current",
    "price_old",
    "discount_pct",
    "discount_amount",
    "installment",
    "category",
    "url",
    "image_url",
    "page",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def clean_price(text: str) -> str:
    """Return numeric string from price like '599,99 ₼'."""
    return re.sub(r"[^\d,.]", "", text).replace(",", ".").strip()


def parse_products(html: str, page_num: int) -> list[dict]:
    """Extract all product dicts from one page of HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for item in soup.select(".product-item"):
        # ── GTM data (name, brand, sku, price, discount, category) ──────────
        gtm_raw = item.get("data-gtm", "{}")
        try:
            gtm = json.loads(gtm_raw)
        except json.JSONDecodeError:
            gtm = {}

        name = gtm.get("item_name", "").strip()
        brand = gtm.get("item_brand", "").strip()
        sku = gtm.get("item_id", item.get("data-sku", "")).strip()
        price_current = gtm.get("price", "")
        discount_amount = gtm.get("discount", 0)
        category = gtm.get("item_category", "").strip()

        # ── fallback name from DOM ────────────────────────────────────────
        if not name:
            title_el = item.select_one(".prodItem__title")
            name = title_el.get_text(strip=True) if title_el else ""

        # ── old / current prices from DOM ─────────────────────────────────
        prices_el = item.select_one(".prodItem__prices")
        price_old = ""
        if prices_el:
            old_el = prices_el.select_one("i")
            cur_el = prices_el.select_one("b")
            if old_el:
                price_old = clean_price(old_el.get_text())
            if cur_el and not price_current:
                price_current = clean_price(cur_el.get_text())

        # ── installment label ─────────────────────────────────────────────
        inst_el = item.select_one(".prodItem__prices span")
        installment = inst_el.get_text(strip=True) if inst_el else ""

        # ── discount % ────────────────────────────────────────────────────
        disc_el = item.select_one(".prodItem__img .label-image-wrapper, "
                                   "[class*='discount'], [class*='label']")
        discount_pct = ""
        if disc_el:
            m = re.search(r"-?\d+\s*%", disc_el.get_text())
            if m:
                discount_pct = m.group().strip()

        # ── URL ───────────────────────────────────────────────────────────
        url = ""
        for a in item.select("a"):
            href = a.get("href", "")
            if href and href != "#" and href.startswith(("http", "/")):
                if not any(k in href for k in ["compare", "wishlist", "cart"]):
                    url = href if href.startswith("http") else BASE_URL + href
                    break

        # ── image ─────────────────────────────────────────────────────────
        image_url = ""
        for img in item.select("img"):
            src = img.get("src", img.get("data-src", ""))
            if "media/catalog" in src:
                image_url = src
                break

        products.append({
            "name": name,
            "brand": brand,
            "sku": sku,
            "price_current": price_current,
            "price_old": price_old,
            "discount_pct": discount_pct,
            "discount_amount": discount_amount,
            "installment": installment,
            "category": category,
            "url": url,
            "image_url": image_url,
            "page": page_num,
        })

    return products


def get_total_pages(html: str) -> int:
    """Determine total number of pages from the listing page."""
    soup = BeautifulSoup(html, "html.parser")

    # Try to get total product count from catalog__count
    count_el = soup.select_one(".catalog__count")
    if count_el:
        m = re.search(r"\d+", count_el.get_text())
        if m:
            total = int(m.group())
            # 20 products per page
            pages = (total + 19) // 20
            print(f"  Total products: {total}  →  {pages} page(s)")
            return pages

    # Fallback: find last numbered page link
    nums = []
    for a in soup.select("a[href*='?p='], a[href*='&p=']"):
        m = re.search(r"[?&]p=(\d+)", a.get("href", ""))
        if m:
            nums.append(int(m.group(1)))
    if nums:
        return max(nums)

    return 1


# ── async fetch ──────────────────────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, str]:
    """Fetch a single listing page; return (page_number, html)."""
    url = f"{CATEGORY_URL}?p={page}"
    async with sem:
        await asyncio.sleep(DELAY)
        async with session.get(url, headers=HEADERS) as resp:
            resp.raise_for_status()
            html = await resp.text()
            print(f"  Fetched page {page}  [status {resp.status}]")
            return page, html


async def get_session_cookies() -> dict:
    """
    Attempt to prime cookies by visiting the homepage first.
    Falls back to curl_cffi if aiohttp is blocked by Cloudflare.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                BASE_URL, headers=HEADERS, allow_redirects=True
            ) as resp:
                if resp.status < 400:
                    cookies = {k: v.value for k, v in session.cookie_jar._cookies.items()
                               if hasattr(v, "value")}
                    return cookies
    except Exception:
        pass
    return {}


async def scrape_all() -> list[dict]:
    """Orchestrate fetching all pages concurrently and parsing products."""
    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    # ── step 1: fetch page 1 to discover total pages ─────────────────────
    print("Fetching page 1 …")
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            _, html1 = await fetch_page(session, 1, asyncio.Semaphore(1))
            total_pages = get_total_pages(html1)
            products1 = parse_products(html1, 1)
            all_products.extend(products1)
            print(f"  Page 1: {len(products1)} products")

            if total_pages < 2:
                return all_products

            # ── step 2: fetch remaining pages concurrently ───────────────
            print(f"Fetching pages 2–{total_pages} (concurrency={CONCURRENCY}) …")
            tasks = [
                fetch_page(session, p, sem)
                for p in range(2, total_pages + 1)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] page fetch error: {result}")
                    continue
                page_num, html = result
                prods = parse_products(html, page_num)
                all_products.extend(prods)
                print(f"  Page {page_num}: {len(prods)} products")

    except aiohttp.ClientResponseError as e:
        if e.status == 403:
            print(
                "\n  [403] Cloudflare blocked aiohttp — "
                "falling back to curl_cffi …\n"
            )
            return await scrape_all_cffi()
        raise

    return all_products


# ── curl_cffi fallback (Chrome TLS impersonation) ────────────────────────────

async def scrape_all_cffi() -> list[dict]:
    """Fallback: use curl_cffi async to bypass Cloudflare."""
    from curl_cffi.requests import AsyncSession

    all_products: list[dict] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def fetch_cffi(session: AsyncSession, page: int) -> tuple[int, str]:
        url = f"{CATEGORY_URL}?p={page}"
        async with sem:
            await asyncio.sleep(DELAY)
            resp = await session.get(url, headers=HEADERS)
            resp.raise_for_status()
            print(f"  [cffi] Fetched page {page}  [status {resp.status_code}]")
            return page, resp.text

    async with AsyncSession(impersonate="chrome124") as session:
        # prime cookies
        await session.get(BASE_URL, headers=HEADERS)

        print("Fetching page 1 (cffi) …")
        _, html1 = await fetch_cffi(session, 1)
        total_pages = get_total_pages(html1)
        products1 = parse_products(html1, 1)
        all_products.extend(products1)
        print(f"  Page 1: {len(products1)} products")

        if total_pages >= 2:
            tasks = [fetch_cffi(session, p) for p in range(2, total_pages + 1)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] {result}")
                    continue
                page_num, html = result
                prods = parse_products(html, page_num)
                all_products.extend(prods)
                print(f"  Page {page_num}: {len(prods)} products")

    return all_products


# ── CSV writer ───────────────────────────────────────────────────────────────

def save_csv(products: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(products)
    print(f"\nSaved {len(products)} products → {path}")


# ── entry point ──────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"Scraping: {CATEGORY_URL}")
    products = await scrape_all()
    if not products:
        print("No products found — check selectors or connectivity.")
        return
    # deduplicate by sku
    seen: set[str] = set()
    unique = []
    for p in products:
        key = p["sku"] or p["url"]
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
        elif not key:
            unique.append(p)
    print(f"\nTotal unique products: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    asyncio.run(main())
