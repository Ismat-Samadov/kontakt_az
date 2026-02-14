"""
tap.az tablet scraper
Uses asyncio + aiohttp (with curl_cffi for Cloudflare bypass)

API: POST https://tap.az/graphql  (GraphQL)
  Operation : GetAds_LATEST
  Category  : gid://tap/Category/616  (plansetler)
  Pagination: cursor-based — pageInfo.endCursor / pageInfo.hasNextPage
              Cursor is base64(offset), e.g. "MzY" = base64("36")

NOTE: tap.az is a marketplace (classified ads), so product data reflects
individual seller listings rather than canonical store inventory.
"""

import asyncio
import csv
import json
from pathlib import Path

import aiohttp

# ── paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_CSV = DATA_DIR / "tapaz.csv"

# ── constants ────────────────────────────────────────────────────────────────
BASE_URL     = "https://tap.az"
GRAPHQL_URL  = f"{BASE_URL}/graphql"
LISTING_URL  = f"{BASE_URL}/elanlar/elektronika/plansetler"
CATEGORY_ID  = "Z2lkOi8vdGFwL0NhdGVnb3J5LzYxNg"   # gid://tap/Category/616
PAGE_SIZE    = 36
CONCURRENCY  = 2     # GraphQL POST — be a bit more conservative
DELAY        = 1.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/144.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,ru;q=0.7,az;q=0.6",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Origin": BASE_URL,
    "Referer": LISTING_URL,
    "sec-ch-ua": '"Not(A:Brand";v="8","Chromium";v="144","Google Chrome";v="144"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "DNT": "1",
}

GQL_QUERY = """\
fragment AdBaseFields on Ad {
  id
  title
  price
  updatedAt
  region
  path
  kinds
  legacyResourceId
  isBookmarked
  shop { id __typename }
  photo { url __typename }
  status
  __typename
}

query GetAds_LATEST(
  $adKind: AdKindEnum, $orderType: AdOrderEnum, $keywords: String,
  $first: Int, $after: String, $source: SourceEnum!,
  $filters: AdFilterInput, $keywordsSource: KeywordSourceEnum,
  $sourceLink: String
) {
  ads(
    adKind: $adKind
    first: $first
    after: $after
    source: $source
    orderType: $orderType
    keywords: $keywords
    filters: $filters
    keywordsSource: $keywordsSource
    sourceLink: $sourceLink
  ) {
    nodes { ...AdBaseFields __typename }
    pageInfo { endCursor hasNextPage __typename }
    __typename
  }
}
"""

CSV_FIELDS = [
    "title",
    "product_id",
    "price",
    "region",
    "updated_at",
    "kinds",
    "status",
    "shop_id",
    "url",
    "image_url",
    "batch",
]


# ── helpers ──────────────────────────────────────────────────────────────────

def build_payload(after: str | None) -> dict:
    """Build GraphQL request body for one page."""
    return {
        "operationName": "GetAds_LATEST",
        "variables": {
            "first": PAGE_SIZE,
            "filters": {
                "categoryId": CATEGORY_ID,
                "price":      {"from": None, "to": None},
                "regionId":   None,
                "propertyOptions": {
                    "collection": [],
                    "boolean":    [],
                    "range":      [],
                },
            },
            "sourceLink": LISTING_URL,
            "source":     "DESKTOP",
            "after":      after,
        },
        "query": GQL_QUERY,
    }


def parse_nodes(nodes: list[dict], batch_num: int) -> list[dict]:
    """Map GraphQL ad nodes to flat CSV rows."""
    rows = []
    for node in nodes:
        path = node.get("path", "")
        url  = BASE_URL + path if path.startswith("/") else path

        shop = node.get("shop") or {}
        shop_id = shop.get("id", "")

        photo = node.get("photo") or {}
        image_url = photo.get("url", "")

        kinds = node.get("kinds") or []
        kinds_str = ", ".join(kinds)

        rows.append({
            "title":       node.get("title", "").strip(),
            "product_id":  node.get("legacyResourceId", ""),
            "price":       node.get("price", ""),
            "region":      node.get("region", "").strip(),
            "updated_at":  node.get("updatedAt", ""),
            "kinds":       kinds_str,
            "status":      node.get("status", ""),
            "shop_id":     shop_id,
            "url":         url,
            "image_url":   image_url,
            "batch":       batch_num,
        })
    return rows


# ── async fetch (sequential — cursor chain cannot be parallelised) ────────────

async def fetch_batch(
    session: aiohttp.ClientSession,
    after: str | None,
    batch_num: int,
) -> tuple[list[dict], str | None, bool]:
    """
    Fetch one GraphQL batch.
    Returns (rows, next_cursor, has_next_page).
    """
    payload = build_payload(after)
    async with session.post(
        GRAPHQL_URL,
        json=payload,
        headers=HEADERS,
        ssl=False,
    ) as resp:
        resp.raise_for_status()
        data = await resp.json(content_type=None)

    ads_data  = data.get("data", {}).get("ads", {})
    nodes     = ads_data.get("nodes", [])
    page_info = ads_data.get("pageInfo", {})
    end_cursor    = page_info.get("endCursor")
    has_next_page = page_info.get("hasNextPage", False)

    rows = parse_nodes(nodes, batch_num)
    print(f"  Batch {batch_num}: {len(rows)} ads  "
          f"[cursor={after!r} → {end_cursor!r}, hasNext={has_next_page}]")
    return rows, end_cursor, has_next_page


async def scrape_all() -> list[dict]:
    """Walk all GraphQL cursor pages sequentially (cursor chain)."""
    connector = aiohttp.TCPConnector(ssl=False)
    timeout   = aiohttp.ClientTimeout(total=15, connect=8)
    all_rows: list[dict] = []

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

            cursor    = None
            has_next  = True
            batch_num = 1

            while has_next:
                await asyncio.sleep(DELAY)
                rows, cursor, has_next = await fetch_batch(
                    session, cursor, batch_num
                )
                all_rows.extend(rows)
                batch_num += 1

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

    return all_rows


# ── curl_cffi fallback ────────────────────────────────────────────────────────

async def scrape_all_cffi() -> list[dict]:
    """Fallback: curl_cffi with Chrome TLS impersonation."""
    from curl_cffi.requests import AsyncSession

    all_rows: list[dict] = []

    async with AsyncSession(impersonate="chrome124") as session:
        # Prime session
        await session.get(
            LISTING_URL,
            headers={**HEADERS, "Accept": "text/html,*/*"},
        )

        cursor    = None
        has_next  = True
        batch_num = 1

        while has_next:
            await asyncio.sleep(DELAY)
            payload = build_payload(cursor)
            resp = await session.post(
                GRAPHQL_URL, json=payload, headers=HEADERS
            )
            resp.raise_for_status()
            data = resp.json()

            ads_data  = data.get("data", {}).get("ads", {})
            nodes     = ads_data.get("nodes", [])
            page_info = ads_data.get("pageInfo", {})
            cursor    = page_info.get("endCursor")
            has_next  = page_info.get("hasNextPage", False)

            rows = parse_nodes(nodes, batch_num)
            print(f"  [cffi] Batch {batch_num}: {len(rows)} ads  "
                  f"[hasNext={has_next}]")
            all_rows.extend(rows)
            batch_num += 1

    return all_rows


# ── CSV writer ────────────────────────────────────────────────────────────────

def save_csv(products: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(products)
    print(f"\nSaved {len(products)} ads → {path}")


# ── entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"Scraping: {LISTING_URL}")
    print(f"Category: gid://tap/Category/616  (page size: {PAGE_SIZE})")
    rows = await scrape_all()

    if not rows:
        print("No ads found — check connectivity.")
        return

    # Deduplicate by product_id
    seen: set = set()
    unique = []
    for r in rows:
        key = str(r["product_id"]) if r["product_id"] != "" else r["url"]
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
        elif not key:
            unique.append(r)

    print(f"\nTotal unique ads: {len(unique)}")
    save_csv(unique, OUTPUT_CSV)


if __name__ == "__main__":
    asyncio.run(main())
