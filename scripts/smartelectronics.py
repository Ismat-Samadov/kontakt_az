"""
smartelectronics.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)

Pagination API (0-based pageIndex):
  GET /az/Catalog/Products/LoadMoreVr/smartfon-ve-aksesuarlar/plansetler
      ?pageIndex=N&pageSize=9

Response is an HTML fragment. Pagination ends when
  <div class="shw_more" hidden>False</div>
appears in the response (or the response is nearly empty).
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
OUTPUT_CSV = DATA_DIR / "smartelectronics.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL     = "https://smartelectronics.az"
LISTING_URL  = f"{BASE_URL}/az/smartfon-ve-aksesuarlar/plansetler"
LOAD_MORE_URL = (
    f"{BASE_URL}/az/Catalog/Products/LoadMoreVr"
    "/smartfon-ve-aksesuarlar/plansetler"
)
PAGE_SIZE    = 9
CONCURRENCY  = 3
DELAY        = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
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
}

CSV_FIELDS = [
    "name",
    "product_id",
    "category",
    "price_current",
    "price_old",
    "installment_monthly",
    "installment_term",
    "in_stock",
    "promo_labels",
    "url",
    "image_url",
    "page",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def clean_price(text: str) -> str:
    """'799 AZN' → '799.0',  '44,39 AZN' → '44.39'"""
    return re.sub(r"[^\d,.]", "", text).replace(",", ".").strip()


def has_more_pages(html: str) -> bool:
    """
    Returns True when more pages are available.
    The API embeds <div class="shw_more" hidden>True/False</div>.
    """
    soup = BeautifulSoup(html, "html.parser")
    el = soup.select_one(".shw_more")
    if el is None:
        # No indicator at all — treat as no more pages (safety fallback)
        return False
    return el.get_text(strip=True).strip().lower() == "true"


def parse_products(html: str, page_num: int) -> list[dict]:
    """Extract all product dicts from one LoadMoreVr HTML fragment."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for card in soup.select(".product_card"):
        # ── product URL (from product_img link) ───────────────────────
        url = ""
        img_link = card.select_one(".product_img > a[href]")
        if img_link:
            href = img_link.get("href", "")
            url = href if href.startswith("http") else BASE_URL + href

        # ── image ─────────────────────────────────────────────────────
        image_url = ""
        img_tag = card.select_one(".product_img img")
        if img_tag:
            image_url = img_tag.get("src", "")

        # ── product ID (from compare/basket links or data-id attr) ────
        product_id = ""
        compare_link = card.select_one("a.add-to-compare[href]")
        if compare_link:
            m = re.search(r"/(\d+)$", compare_link.get("href", ""))
            if m:
                product_id = m.group(1)
        if not product_id:
            price_p = card.select_one(".product_price p[data-id]")
            if price_p:
                product_id = price_p.get("data-id", "")

        # ── category & name ───────────────────────────────────────────
        category = ""
        name = ""
        title_div = card.select_one(".product_title")
        if title_div:
            cat_el = title_div.select_one("span")
            name_el = title_div.select_one("p")
            category = cat_el.get_text(strip=True) if cat_el else ""
            name = name_el.get_text(strip=True) if name_el else ""

        # Fallback name from data-product-name attribute
        if not name:
            btn = card.select_one("[data-product-name]")
            if btn:
                name = btn.get("data-product-name", "")

        # ── prices ────────────────────────────────────────────────────
        price_current = ""
        price_old = ""
        price_div = card.select_one(".product_price")
        if price_div:
            old_el = price_div.select_one("span")
            cur_el = price_div.select_one("p")
            if old_el:
                price_old = clean_price(old_el.get_text())
            if cur_el:
                price_current = clean_price(cur_el.get_text())

        # ── installment ───────────────────────────────────────────────
        installment_monthly = ""
        installment_term = ""
        credit_div = card.select_one(".product_credit")
        if credit_div:
            monthly_el = credit_div.select_one("p[data-target]")
            if monthly_el:
                installment_monthly = clean_price(monthly_el.get_text())
            active_item = credit_div.select_one(
                ".product__credit_list_item.active"
            )
            if active_item:
                installment_term = active_item.get_text(strip=True)

        # ── in-stock status ───────────────────────────────────────────
        in_stock = ""
        btn_div = card.select_one("[data-product-out-of-stock]")
        if btn_div:
            oos = btn_div.get("data-product-out-of-stock", "").strip().lower()
            in_stock = "False" if oos == "true" else "True"

        # ── promo / badge labels ──────────────────────────────────────
        promo_labels = "; ".join(
            s.get_text(strip=True)
            for s in card.select(".product_percent .swiper-slide")
            if s.get_text(strip=True)
        )

        products.append({
            "name": name,
            "product_id": product_id,
            "category": category,
            "price_current": price_current,
            "price_old": price_old,
            "installment_monthly": installment_monthly,
            "installment_term": installment_term,
            "in_stock": in_stock,
            "promo_labels": promo_labels,
            "url": url,
            "image_url": image_url,
            "page": page_num,
        })

    return products


# ── async fetch ──────────────────────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    page_index: int,
    sem: asyncio.Semaphore,
) -> tuple[int, str]:
    """Fetch one LoadMoreVr page fragment; return (page_index, html)."""
    params = {"pageIndex": page_index, "pageSize": PAGE_SIZE}
    async with sem:
        await asyncio.sleep(DELAY)
        async with session.get(
            LOAD_MORE_URL, params=params, headers=HEADERS, ssl=False
        ) as resp:
            resp.raise_for_status()
            html = await resp.text()
            print(f"  Fetched pageIndex={page_index}  [status {resp.status}]")
            return page_index, html


async def scrape_all() -> list[dict]:
    """Orchestrate fetching all pages and parsing products."""
    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    connector = aiohttp.TCPConnector(ssl=False)
    # Use a short connect timeout — Cloudflare often hangs rather than 403-ing
    timeout   = aiohttp.ClientTimeout(total=15, connect=8)

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            # Prime session cookies by visiting the listing page first
            print("Priming session cookies …")
            async with session.get(
                LISTING_URL, headers={**HEADERS, "Accept": "text/html,*/*"}, ssl=False
            ) as r:
                r.raise_for_status()

            # Fetch pageIndex=0 first to check shw_more
            print("Fetching pageIndex=0 …")
            _, html0 = await fetch_page(session, 0, asyncio.Semaphore(1))
            prods0 = parse_products(html0, 0)
            all_products.extend(prods0)
            print(f"  pageIndex=0: {len(prods0)} products")

            if not has_more_pages(html0):
                return all_products

            # Walk remaining pages concurrently in batches
            page_index = 1
            while True:
                tasks = [
                    fetch_page(session, p, sem)
                    for p in range(page_index, page_index + CONCURRENCY)
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                found_more = False
                for result in sorted(
                    [r for r in results if not isinstance(r, Exception)],
                    key=lambda x: x[0],
                ):
                    p_idx, html = result
                    prods = parse_products(html, p_idx)
                    all_products.extend(prods)
                    print(f"  pageIndex={p_idx}: {len(prods)} products")
                    page_index = max(page_index, p_idx + 1)
                    found_more = bool(prods) and has_more_pages(html)
                    if not found_more:
                        break

                for result in results:
                    if isinstance(result, Exception):
                        print(f"  [warn] page fetch error: {result}")

                if not found_more:
                    break

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

    async def fetch_cffi(s, page_index: int) -> tuple[int, str]:
        params = {"pageIndex": page_index, "pageSize": PAGE_SIZE}
        async with sem:
            await asyncio.sleep(DELAY)
            resp = await s.get(LOAD_MORE_URL, params=params, headers=HEADERS)
            resp.raise_for_status()
            print(f"  [cffi] pageIndex={page_index}  [{resp.status_code}]")
            return page_index, resp.text

    async with AsyncSession(impersonate="chrome124") as s:
        # Prime cookies
        await s.get(
            LISTING_URL,
            headers={**HEADERS, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
        )

        print("Fetching pageIndex=0 (cffi) …")
        _, html0 = await fetch_cffi(s, 0)
        prods0 = parse_products(html0, 0)
        all_products.extend(prods0)
        print(f"  pageIndex=0: {len(prods0)} products")

        if not has_more_pages(html0):
            return all_products

        page_index = 1
        while True:
            tasks = [fetch_cffi(s, p) for p in range(page_index, page_index + CONCURRENCY)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            found_more = False
            for result in sorted(
                [r for r in results if not isinstance(r, Exception)],
                key=lambda x: x[0],
            ):
                p_idx, html = result
                prods = parse_products(html, p_idx)
                all_products.extend(prods)
                print(f"  pageIndex={p_idx}: {len(prods)} products")
                page_index = max(page_index, p_idx + 1)
                found_more = bool(prods) and has_more_pages(html)
                if not found_more:
                    break

            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] {result}")

            if not found_more:
                break

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
