"""
birmarket.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)

Pagination: GET /categories/17-planshetler?page=N  (1-indexed)
  - Page 1: no query param needed (or ?page=1 also works)
  - Last page: taken from the highest numbered link in .MPProductPagination

Product card selector: .MPProductItem
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
OUTPUT_CSV = DATA_DIR / "birmarket.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL     = "https://birmarket.az"
CATEGORY_URL = f"{BASE_URL}/categories/17-planshetler"
CONCURRENCY  = 3
DELAY        = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
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
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
}

CSV_FIELDS = [
    "name",
    "product_id",
    "price_current",
    "price_old",
    "discount_pct",
    "installment_monthly",
    "installment_term",
    "url",
    "image_url",
    "page",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def clean_price(text: str) -> str:
    """'169.00 ₼' → '169.00'   '1.299,99 ₼' → '1299.99'"""
    text = re.sub(r"[^\d,.]", "", text).strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    parts = text.split(".")
    if len(parts) > 2:
        text = "".join(parts[:-1]) + "." + parts[-1]
    return text


def parse_installment(text: str) -> tuple[str, str]:
    """
    '7.05 ₼ x 24 ay'  →  ('7.05', '24 ay')
    '14,58 ₼ x 12 ay' →  ('14.58', '12 ay')
    Returns ('', '') if no match.
    """
    m = re.search(r"([\d.,]+)\s*[₼₽$]?\s*[xX×]\s*(\d+)\s*ay", text)
    if m:
        monthly = clean_price(m.group(1))
        term    = f"{m.group(2)} ay"
        return monthly, term
    return "", ""


def parse_products(html: str, page_num: int) -> list[dict]:
    """Extract all product dicts from one page of HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for item in soup.select(".MPProductItem"):
        # ── product ID ────────────────────────────────────────────────
        product_id = item.get("data-product-id", "").strip()

        # ── URL ───────────────────────────────────────────────────────
        url = ""
        a_tag = item.select_one("a[href]")
        if a_tag:
            href = a_tag.get("href", "")
            url = href if href.startswith("http") else BASE_URL + href

        # ── image (prefer highest-res source) ─────────────────────────
        image_url = ""
        pic = item.select_one("picture")
        if pic:
            # Last <source> is typically the full-res version
            sources = pic.select("source[srcset]")
            if sources:
                image_url = sources[-1].get("srcset", "").split("?")[0].strip()
        if not image_url:
            img = item.select_one("img")
            if img:
                image_url = img.get("src", "").split("?")[0].strip()

        # ── prices ────────────────────────────────────────────────────
        price_current = ""
        price_old     = ""
        cur_el = item.select_one('[data-info="item-desc-price-new"]')
        old_el = item.select_one('[data-info="item-desc-price-old"]')
        if cur_el:
            price_current = clean_price(cur_el.get_text())
        if old_el:
            price_old = clean_price(old_el.get_text())

        # ── discount % ────────────────────────────────────────────────
        disc_el = item.select_one(".MPProductItem-Discount")
        discount_pct = disc_el.get_text(strip=True) if disc_el else ""

        # ── installment ("7.05 ₼ x 24 ay") ───────────────────────────
        installment_monthly = ""
        installment_term    = ""
        inst_el = item.select_one(".MPInstallment")
        if inst_el:
            installment_monthly, installment_term = parse_installment(
                inst_el.get_text(" ", strip=True)
            )

        # ── name ──────────────────────────────────────────────────────
        name = ""
        title_el = item.select_one(".MPTitle")
        if title_el:
            name = title_el.get_text(strip=True)
        if not name and a_tag:
            name = a_tag.get("title", "").strip() or a_tag.get_text(strip=True)

        products.append({
            "name":               name,
            "product_id":         product_id,
            "price_current":      price_current,
            "price_old":          price_old,
            "discount_pct":       discount_pct,
            "installment_monthly": installment_monthly,
            "installment_term":   installment_term,
            "url":                url,
            "image_url":          image_url,
            "page":               page_num,
        })

    return products


def get_total_pages(html: str) -> int:
    """Read the highest page number from .MPProductPagination links."""
    soup = BeautifulSoup(html, "html.parser")
    nums = []
    for a in soup.select(".MPProductPagination-PageItem a[href]"):
        m = re.search(r"[?&]page=(\d+)", a.get("href", ""))
        if m:
            nums.append(int(m.group(1)))
    if nums:
        last = max(nums)
        print(f"  Total pages: {last}")
        return last
    return 1


# ── async fetch ──────────────────────────────────────────────────────────────

async def fetch_page(
    session: aiohttp.ClientSession,
    page: int,
    sem: asyncio.Semaphore,
) -> tuple[int, str]:
    """Fetch one listing page; return (page_number, html)."""
    url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}?page={page}"
    async with sem:
        await asyncio.sleep(DELAY)
        async with session.get(url, headers=HEADERS, ssl=False) as resp:
            resp.raise_for_status()
            html = await resp.text()
            print(f"  Fetched page {page}  [status {resp.status}]")
            return page, html


async def scrape_all() -> list[dict]:
    """Orchestrate fetching all pages concurrently."""
    sem = asyncio.Semaphore(CONCURRENCY)
    all_products: list[dict] = []

    connector = aiohttp.TCPConnector(ssl=False)
    timeout   = aiohttp.ClientTimeout(total=15, connect=8)

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            # Step 1: fetch page 1 to discover last page
            print("Fetching page 1 …")
            _, html1 = await fetch_page(session, 1, asyncio.Semaphore(1))
            total_pages = get_total_pages(html1)
            prods1 = parse_products(html1, 1)
            all_products.extend(prods1)
            print(f"  Page 1: {len(prods1)} products")

            if total_pages < 2:
                return all_products

            # Step 2: fetch remaining pages concurrently
            print(f"Fetching pages 2–{total_pages} (concurrency={CONCURRENCY}) …")
            tasks = [fetch_page(session, p, sem) for p in range(2, total_pages + 1)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    print(f"  [warn] page fetch error: {result}")
                    continue
                page_num, html = result
                prods = parse_products(html, page_num)
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


# ── curl_cffi fallback ────────────────────────────────────────────────────────

async def scrape_all_cffi() -> list[dict]:
    """Fallback: use curl_cffi async to bypass Cloudflare."""
    from curl_cffi.requests import AsyncSession

    all_products: list[dict] = []
    sem = asyncio.Semaphore(CONCURRENCY)

    async def fetch_cffi(s, page: int) -> tuple[int, str]:
        url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}?page={page}"
        async with sem:
            await asyncio.sleep(DELAY)
            resp = await s.get(url, headers=HEADERS)
            resp.raise_for_status()
            print(f"  [cffi] page {page}  [{resp.status_code}]")
            return page, resp.text

    async with AsyncSession(impersonate="chrome124") as session:
        await session.get(BASE_URL, headers=HEADERS)   # prime cookies

        print("Fetching page 1 (cffi) …")
        _, html1 = await fetch_cffi(session, 1)
        total_pages = get_total_pages(html1)
        prods1 = parse_products(html1, 1)
        all_products.extend(prods1)
        print(f"  Page 1: {len(prods1)} products")

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
