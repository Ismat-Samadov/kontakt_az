"""
bytelecom.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)

Listing: GET https://bytelecom.az/az/category/plansetler?page=N
  Pagination: ?page=N (Bootstrap pagination)
  Last page: max numeric value among ul.pagination button.page-link buttons

Product cards use Livewire components; key data lives in plain HTML attributes:
  - Product ID : wire:click="toggleWishlist(ID)"
  - Name       : a.product-name
  - URL        : a[href] wrapping .product-img
  - Image      : .product-img img[src]
  - Old price  : .prices h6.discount-price   (original / before discount)
  - Sale price : .prices h5.price            (current / discounted)
  - Badges     : .badge-item p               (e.g. "İlkin ödənişsiz və Faizsiz")
  - New flag   : .new-product p              ("Yeni")
"""

import asyncio
import csv
import re
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

# ── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_CSV = DATA_DIR / "bytelecom.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL     = "https://bytelecom.az"
CATEGORY_URL = f"{BASE_URL}/az/category/plansetler"
CONCURRENCY  = 3
DELAY        = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "az,en-US;q=0.9,en;q=0.8,ru;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": BASE_URL + "/",
    "sec-ch-ua": '"Not(A:Brand";v="8","Chromium";v="144","Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
}

CSV_FIELDS = [
    "name",
    "product_id",
    "price_current",
    "price_old",
    "badges",
    "is_new",
    "url",
    "image_url",
    "page",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def clean_price(text: str) -> str:
    """'₼ 2,499.00' → '2499.00'"""
    return re.sub(r"[^\d.]", "", text.replace(",", "")).strip()


def parse_last_page(soup: BeautifulSoup) -> int:
    """Return the highest page number found in pagination buttons."""
    nums = []
    for btn in soup.select("ul.pagination li button.page-link"):
        t = btn.get_text(strip=True)
        if t.isdigit():
            nums.append(int(t))
    # Current page is rendered differently (active li, not a button)
    # Also check active page item text
    for li in soup.select("ul.pagination li.page-item"):
        t = li.get_text(strip=True)
        if t.isdigit():
            nums.append(int(t))
    return max(nums) if nums else 1


def parse_products(html: str, page: int) -> list[dict]:
    """Extract all product dicts from one listing page."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select(".categorised-products .product"):
        # ── product ID ────────────────────────────────────────────────
        product_id = ""
        # Extract from wire:click="toggleWishlist(ID)"
        wc = card.get("wire:click", "") or ""
        m = re.search(r"toggleWishlist\((\d+)\)", wc)
        if not m:
            # Search inside the card for the wishlist button
            wb = card.select_one("button.favourite-product[wire\\:click]")
            if wb:
                m = re.search(r"toggleWishlist\((\d+)\)", wb.get("wire:click", ""))
        if m:
            product_id = m.group(1)

        # ── URL ───────────────────────────────────────────────────────
        url = ""
        a_el = card.select_one(".product-img")
        if a_el:
            parent = a_el.parent
            if parent and parent.name == "a":
                href = parent.get("href", "")
                url = href if href.startswith("http") else BASE_URL + href
        if not url:
            a_el2 = card.select_one("a[href*='/az/products/']")
            if a_el2:
                href = a_el2.get("href", "")
                url = href if href.startswith("http") else BASE_URL + href

        # ── image ─────────────────────────────────────────────────────
        image_url = ""
        img = card.select_one(".product-img img")
        if img:
            src = img.get("src", "").strip()
            image_url = src if src.startswith("http") else BASE_URL + src

        # ── name ──────────────────────────────────────────────────────
        name = ""
        name_el = card.select_one("a.product-name")
        if name_el:
            name = name_el.get_text(strip=True)

        # ── prices ────────────────────────────────────────────────────
        # h6.discount-price = original (higher) price
        # h5.price          = current (lower/discounted) price
        price_old     = ""
        price_current = ""
        old_el = card.select_one(".prices h6.discount-price")
        cur_el = card.select_one(".prices h5.price")
        if old_el:
            price_old     = clean_price(old_el.get_text())
        if cur_el:
            price_current = clean_price(cur_el.get_text())

        # ── badges ────────────────────────────────────────────────────
        badge_texts = [
            el.get_text(strip=True)
            for el in card.select(".badge-item p")
            if el.get_text(strip=True)
        ]
        badges = "; ".join(badge_texts)

        # ── new flag ──────────────────────────────────────────────────
        is_new = "True" if card.select_one(".new-product") else "False"

        products.append({
            "name":          name,
            "product_id":    product_id,
            "price_current": price_current,
            "price_old":     price_old,
            "badges":        badges,
            "is_new":        is_new,
            "url":           url,
            "image_url":     image_url,
            "page":          page,
        })

    return products


# ── async fetch ──────────────────────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, str]:
    """GET one listing page; return (page, html)."""
    url = f"{CATEGORY_URL}?page={page}"
    async with sem:
        await asyncio.sleep(DELAY)
        async with session.get(url, headers=HEADERS, ssl=False) as resp:
            resp.raise_for_status()
            html = await resp.text()
            print(f"  page={page}  status={resp.status}")
            return page, html


async def scrape_all() -> list[dict]:
    """Fetch page 1 to discover last page, then all pages concurrently."""
    sem       = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(ssl=False)
    timeout   = aiohttp.ClientTimeout(total=15, connect=8)
    all_products: list[dict] = []

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            # Page 1: discover last page
            print("Fetching page 1 …")
            _, html1 = await fetch_page(session, 1, asyncio.Semaphore(1))
            soup1    = BeautifulSoup(html1, "html.parser")
            last_page = parse_last_page(soup1)
            print(f"  last_page={last_page}")
            all_products.extend(parse_products(html1, 1))

            if last_page > 1:
                tasks   = [fetch_page(session, p, sem) for p in range(2, last_page + 1)]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in sorted(
                    [r for r in results if not isinstance(r, Exception)],
                    key=lambda x: x[0],
                ):
                    pg, html = result
                    all_products.extend(parse_products(html, pg))
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
    """Fallback: curl_cffi with Chrome TLS impersonation."""
    from curl_cffi.requests import AsyncSession

    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    async def fetch_cffi(s, page: int) -> tuple[int, str]:
        async with sem:
            await asyncio.sleep(DELAY)
            url  = f"{CATEGORY_URL}?page={page}"
            resp = await s.get(url, headers=HEADERS)
            resp.raise_for_status()
            print(f"  [cffi] page={page}  status={resp.status_code}")
            return page, resp.text

    async with AsyncSession(impersonate="chrome124") as s:
        _, html1   = await fetch_cffi(s, 1)
        soup1      = BeautifulSoup(html1, "html.parser")
        last_page  = parse_last_page(soup1)
        print(f"  last_page={last_page}")
        all_products.extend(parse_products(html1, 1))

        if last_page > 1:
            tasks   = [fetch_cffi(s, p) for p in range(2, last_page + 1)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in sorted(
                [r for r in results if not isinstance(r, Exception)],
                key=lambda x: x[0],
            ):
                pg, html = result
                all_products.extend(parse_products(html, pg))
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
    print(f"Scraping: {CATEGORY_URL}")
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
