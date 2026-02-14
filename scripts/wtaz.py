"""
w-t.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)

Single-page listing (no pagination):
  GET https://www.w-t.az/k3+plansetler
  Product count is served SSR inside .filterProducts .item cards.

Each card contains:
  - Product ID   : button.addToFavourite[data-id]
  - Name         : .productName
  - URL          : a.productUrl[href]
  - Image        : .productImage-img[src]
  - Price        : .realPrice  (integer + <sup>.decimal</sup> + ₼)
  - Installments : label.month[data-price] for 6/12/18 ay options
  - Active term  : label.month.checked  (default shown term)
  - Campaign     : .cashCampaign p  (e.g. "Pulsuz çatdırılma")
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
OUTPUT_CSV = DATA_DIR / "wtaz.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL     = "https://www.w-t.az"
CATEGORY_URL = f"{BASE_URL}/k3+plansetler"

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
    "price",
    "installment_6m",
    "installment_12m",
    "installment_18m",
    "installment_active_term",
    "installment_active_price",
    "campaign",
    "url",
    "image_url",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def parse_price(el) -> str:
    """
    Extract price from .realPrice which looks like:
      959<sup>.00</sup>₼
    Returns '959.00'.
    """
    if not el:
        return ""
    # Clone to avoid mutating the tree
    text = el.get_text(separator="", strip=True)
    # Remove currency symbol and whitespace
    text = re.sub(r"[₼\s]", "", text)
    # text may be '959.00' or '959,00' — normalise
    return text.replace(",", ".")


def parse_products(html: str) -> list[dict]:
    """Extract all product dicts from the listing page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    products = []

    for item in soup.select(".filterProducts .item"):
        card = item.select_one(".productCard")
        if not card:
            continue

        # ── product ID ────────────────────────────────────────────────
        fav_btn = card.select_one("button.addToFavourite[data-id]")
        product_id = fav_btn.get("data-id", "").strip() if fav_btn else ""

        # ── URL ───────────────────────────────────────────────────────
        url = ""
        url_a = card.select_one("a.productUrl[href]")
        if url_a:
            href = url_a.get("href", "")
            url = href if href.startswith("http") else BASE_URL + href

        # ── image ─────────────────────────────────────────────────────
        image_url = ""
        img = card.select_one(".productImage-img")
        if img:
            image_url = img.get("src", "").strip()

        # ── name ──────────────────────────────────────────────────────
        name = ""
        name_el = card.select_one(".productName")
        if name_el:
            name = name_el.get_text(strip=True)

        # ── price ─────────────────────────────────────────────────────
        price_el = card.select_one(".realPrice")
        price = parse_price(price_el)

        # ── installment options (6 / 12 / 18 ay) ─────────────────────
        installments: dict[str, str] = {}
        for label in card.select("label.month[data-price]"):
            months_text = label.get_text(strip=True)   # "6 ay", "12 ay", "18 ay"
            m = re.search(r"(\d+)\s*ay", months_text)
            if m:
                installments[m.group(1)] = label.get("data-price", "")

        # Active (currently displayed) term
        active_label = card.select_one("label.month.checked")
        active_term  = ""
        active_price = ""
        if active_label:
            m = re.search(r"(\d+)\s*ay", active_label.get_text(strip=True))
            active_term  = f"{m.group(1)} ay" if m else ""
            active_price = active_label.get("data-price", "")

        # ── campaign labels ───────────────────────────────────────────
        campaign_labels = [
            p.get_text(strip=True)
            for p in card.select(".cashCampaign p, .labels p")
            if p.get_text(strip=True)
        ]
        campaign = "; ".join(campaign_labels)

        products.append({
            "name":                   name,
            "product_id":             product_id,
            "price":                  price,
            "installment_6m":         installments.get("6", ""),
            "installment_12m":        installments.get("12", ""),
            "installment_18m":        installments.get("18", ""),
            "installment_active_term":  active_term,
            "installment_active_price": active_price,
            "campaign":               campaign,
            "url":                    url,
            "image_url":              image_url,
        })

    return products


# ── async fetch ──────────────────────────────────────────────────────────────

async def scrape_all() -> list[dict]:
    """Fetch the single listing page and parse all products."""
    connector = aiohttp.TCPConnector(ssl=False)
    timeout   = aiohttp.ClientTimeout(total=15, connect=8)

    try:
        async with aiohttp.ClientSession(
            connector=connector, timeout=timeout
        ) as session:
            async with session.get(
                CATEGORY_URL, headers=HEADERS, ssl=False
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
                print(f"  Fetched listing  [status {resp.status}]")

        products = parse_products(html)
        return products

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


# ── curl_cffi fallback ────────────────────────────────────────────────────────

async def scrape_all_cffi() -> list[dict]:
    """Fallback: curl_cffi with Chrome TLS impersonation."""
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome124") as session:
        resp = await session.get(CATEGORY_URL, headers=HEADERS)
        resp.raise_for_status()
        print(f"  [cffi] Fetched listing  [{resp.status_code}]")
        return parse_products(resp.text)


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

    # Deduplicate by product_id
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
