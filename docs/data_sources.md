# Data Sources — Technical Reference

Each section documents one scraper: the endpoint it hits, the pagination mechanism, the HTML/JSON selectors it relies on, and any known quirks or limitations.

---

## Table of Contents

1. [irshad.az](#1-irshadaz)
2. [kontakt.az](#2-kontaktaz)
3. [smartelectronics.az](#3-smartelectronicsaz)
4. [bakuelectronics.az](#4-bakuelectronicsaz)
5. [mgstore.az](#5-mgstoreaz)
6. [birmarket.az](#6-birmarketaz)
7. [tap.az](#7-tapaz)
8. [w-t.az](#8-w-taz)
9. [soliton.az](#9-solitonaz)
10. [bytelecom.az](#10-bytelecomaz)
11. [texnohome.az](#11-texnohomeaz)

---

## 1. irshad.az

**Script:** `scripts/irshad.py`
**Output:** `data/irshad.csv`

### Endpoint

```
GET https://irshad.az/az/catalog/plansetler?page=N
```

### Pagination

Standard `?page=N` query parameter. The last page number is read from the highest numeric page link in `.pagination a[href*="?page="]`. All pages are fetched concurrently after page 1 is retrieved.

### Product Card Selectors

| Field | Selector / Source |
|-------|-------------------|
| Name | `.product-name` or `h3.product-title` |
| Product code | `.product-code` or `data-product-code` attribute |
| Current price | `.price-current` |
| Old price | `.price-old` |
| Discount % | `.discount-badge` |
| Discount amount | computed from prices |
| Availability | `.availability` (`Var` / `Yoxdur`) |
| Installment 6m | `.installment[data-month="6"] .amount` |
| Installment 12m | `.installment[data-month="12"] .amount` |
| Installment 18m | `.installment[data-month="18"] .amount` |
| Product type | `.product-category` breadcrumb |
| URL | `a.product-link[href]` |
| Image | `.product-image img[src]` |

### Known Issues / Notes

- Uses `code` as the product identifier (renamed to `product_id` in `combine.py`).
- `availability` field contains Azerbaijani text (`Var` = in stock, `Yoxdur` = out of stock); renamed to `in_stock` in `combine.py`.
- `product_type` contains the product category string; renamed to `category` in `combine.py`.

---

## 2. kontakt.az

**Script:** `scripts/kontakt.py`
**Output:** `data/kontakt.csv`

### Endpoint

```
GET https://kontakt.az/az/catalog/telefonlar-ve-plansetler/plansetler?page=N
```

### Pagination

`?page=N` parameter. Last page determined from `.pagination` link with the highest integer.

### Product Card Selectors

Product data is embedded as a JSON blob in a `data-gtm` attribute on each `.product-item` card — the same GTM (Google Tag Manager) data layer pattern used across Magento-family stores.

```html
<div class="product-item" data-gtm='{"name":"...","id":"...","price":...}'>
```

| Field | Source |
|-------|--------|
| Name | `data-gtm → name` |
| Brand | `data-gtm → brand` |
| SKU | `data-gtm → id` |
| Current price | `data-gtm → price` (discounted) |
| Old price | `data-gtm → originalPrice` |
| Discount % | `data-gtm → discount` |
| Discount amount | derived |
| Installment | `.installment-block` text |
| Category | `data-gtm → category` |
| URL | `a.product-link[href]` |
| Image | `.product-image img[src]` |

### Known Issues / Notes

- One product (`iPad Mini Wi-Fi + Cellular 128 GB Space Grey MXPN3QA/A`) has `price_current = 0.01`. This is a data artefact from the site (likely a placeholder/discontinued listing) and is filtered in analysis by excluding prices ≤ 1.0 AZN.
- Same GTM data layer pattern as `mgstore.az` — the two stores share catalogue overlap on Apple and Samsung flagship models.

---

## 3. smartelectronics.az

**Script:** `scripts/smartelectronics.py`
**Output:** `data/smartelectronics.csv`

### Endpoint

```
GET https://smartelectronics.az/az/Catalog/Products/LoadMoreVr/smartfon-ve-aksesuarlar/plansetler
    ?pageIndex=N&pageSize=9
```

Partial HTML fragments are returned — each request loads a page of 9 product cards.

### Pagination

- `pageIndex` is 0-based.
- Stop condition: the response HTML contains `<div class="shw_more" hidden>False</div>`.
- All page indexes are fetched concurrently after page 0 is retrieved and total count is known.

### Product Card Selectors

| Field | Selector |
|-------|----------|
| Name | `.product_title p` |
| Product ID | `[data-product-id]` or `.product-btn[data-id]` |
| Category | breadcrumb `.product-category` |
| Current price | `.product_price span.current-price` or `p[data-id]` |
| Old price | `.product_price span.old-price` |
| Installment monthly | `.product_credit .monthly-amount` |
| Installment term | `.product_credit .term` |
| In stock | absence of `.product-btn[data-product-out-of-stock]` attribute |
| Promo labels | `.promo-label` text |
| URL | `a.product-card[href]` |
| Image | `.product-image img[src]` |

### Known Issues / Notes

- `promo_labels` is renamed to `special_offer` in `combine.py`.
- This site is behind Cloudflare; the aiohttp path may silently hang (no 403 is returned). A short `ClientTimeout(total=15, connect=8)` is set so that `asyncio.TimeoutError` triggers the curl_cffi fallback.

---

## 4. bakuelectronics.az

**Script:** `scripts/bakuelectronics.py`
**Output:** `data/bakuelectronics.csv`

### Endpoint — Two-stage

**Stage 1 (page 1 + build ID):**
```
GET https://www.bakuelectronics.az/az/catalog/telefonlar-qadcetler/plansetler
```
The HTML contains a `<script id="__NEXT_DATA__">` tag with a JSON blob that includes the Next.js build ID and the first page of product data.

**Stage 2 (pages 2+):**
```
GET https://www.bakuelectronics.az/_next/data/{buildId}/az/catalog/telefonlar-qadcetler/plansetler.json
    ?slug=telefonlar-qadcetler&slug=plansetler&page=N
```

### Pagination

`buildId` is extracted dynamically via `re.search(r'"buildId":"([^"]+)"', html)` on each scraper run — it changes with every site deployment. Total product count and page size are also read from `__NEXT_DATA__`; last page = `ceil(total / page_size)`.

### JSON Fields (from Next.js data)

| CSV field | JSON path |
|-----------|-----------|
| Name | `product.name` |
| Product ID | `product.id` |
| SKU | `product.sku` |
| Current price | `product.price` |
| Old price | `product.original_price` |
| Discount amount | `product.discount_amount` |
| Installment monthly | `product.installment.monthly` |
| Installment term | `product.installment.term` |
| Quantity | `product.quantity` |
| Review count | `product.review_count` |
| Rating | `product.rating` |
| Is online | `product.is_online` |
| Campaign | `product.campaign.label` |
| URL | `product.url` |
| Image URL | `product.image.url` |

### Known Issues / Notes

- The `buildId` must be re-fetched on every run; hardcoding it will break after any site deployment.
- `campaign` is renamed to `special_offer` in `combine.py`.
- This is the only scraper to capture `review_count`, `rating`, and `quantity` — these fields are blank for all other sources in `data.csv`.

---

## 5. mgstore.az

**Script:** `scripts/mgstore.py`
**Output:** `data/mgstore.csv`

### Endpoint

```
GET https://mgstore.az/plansetler/plansetler?p=N
```

### Pagination

Magento-style `?p=N`. Last page detected from `.pages a.page[href*="?p="]` links — the highest integer found is the last page.

### Product Card Selectors

Same GTM `data-gtm` JSON pattern as kontakt.az.

| Field | Source |
|-------|--------|
| Name | `data-gtm → name` |
| Product ID | `data-gtm → id` |
| SKU | `data-gtm → sku` |
| Brand | `data-gtm → brand` |
| Current price | `data-gtm → price` |
| Old price | `data-gtm → originalPrice` |
| Discount amount | derived |
| Installment | `.installment-info` text |
| Category | `data-gtm → category` |
| URL | `a.product-item-link[href]` |
| Image | `.product-image-photo[src]` |

### Known Issues / Notes

- **European price format:** prices are formatted as `1.899,99 ₼` (dot = thousands separator, comma = decimal). The `clean_price()` function in this script handles the conversion: if a comma is present, all dots are removed and the comma is replaced with a dot.
- kataloque and kontakt.az share many of the same SKUs (identical product IDs), suggesting a common product data source or brand agreement.

---

## 6. birmarket.az

**Script:** `scripts/birmarket.py`
**Output:** `data/birmarket.csv`

### Endpoint

```
GET https://birmarket.az/categories/17-planshetler?page=N
```

### Pagination

`?page=N` parameter. Last page determined by reading the highest page number from `.MPProductPagination-PageItem a[href*="?page="]`. birmarket is the largest source with 582 listings across 28 pages.

### Product Card Selectors

The site is a Vue/Nuxt SSR application. Product cards use `.MPProductItem`.

| Field | Selector |
|-------|----------|
| Name | `.MPProductItem-Name` or `h2.product-name` |
| Product ID | `[data-product-id]` attribute |
| Current price | `[data-info="item-desc-price-new"]` |
| Old price | `[data-info="item-desc-price-old"]` |
| Discount % | `.MPProductItem-Discount` |
| Installment monthly | `.MPInstallment` — parsed from `"7.05 ₼ x 24 ay"` pattern |
| Installment term | extracted from the same installment string |
| URL | `a.MPProductItem-Link[href]` |
| Image | `.MPProductItem-Image img[src]` |

**Installment regex:**
```python
re.search(r"([\d.,]+)\s*[₼₽$]?\s*[xX×]\s*(\d+)\s*ay", text)
```

### Known Issues / Notes

- birmarket functions as a C2C/B2C marketplace (similar to OLX), so individual sellers set their own prices. The same model may appear many times at different price points.
- 171 out of 582 rows have no valid price (`price_current` is empty) — these are listings where the seller has marked the item as "price on request".
- Installment data reflects birmarket's consumer financing partner, not per-seller financing.

---

## 7. tap.az

**Script:** `scripts/tapaz.py`
**Output:** `data/tapaz.csv`

### Endpoint

```
POST https://tap.az/graphql
Content-Type: application/json
```

### GraphQL Operation

```graphql
query GetAds_LATEST(
  $adKind: AdKindEnum, $orderType: AdOrderEnum, $keywords: String,
  $first: Int, $after: String, $source: SourceEnum!,
  $filters: AdFilterInput, ...
) {
  ads(first: $first, after: $after, filters: $filters, ...) {
    nodes { ...AdBaseFields }
    pageInfo { endCursor hasNextPage }
  }
}
```

**Category filter:** `gid://tap/Category/616` (plansetler)
**Page size:** 36 ads per batch

### Pagination

Cursor-based. Each response returns `pageInfo.endCursor` (a base64-encoded offset, e.g. `"MzY"` = `"36"`) and `pageInfo.hasNextPage`. The next request uses the previous `endCursor` as `$after`. Because each cursor depends on the previous response, pages **cannot be fetched in parallel** — they are fetched sequentially.

### JSON Fields

| CSV field | GraphQL field |
|-----------|--------------|
| Title | `node.title` |
| Product ID | `node.legacyResourceId` |
| Price | `node.price` |
| Region | `node.region` |
| Updated at | `node.updatedAt` |
| Kinds | `node.kinds` (array → comma-joined string) |
| Status | `node.status` |
| Shop ID | `node.shop.id` |
| URL | `BASE_URL + node.path` |
| Image URL | `node.photo.url` |

### Known Issues / Notes

- tap.az is a **classified-ad marketplace** — listings include new, used, and grey-import tablets, accessories, cases, and keyboards. Not all listings are actual tablet devices.
- `title` is renamed to `name` in `combine.py`.
- `price` is renamed to `price_current` in `combine.py`.
- `batch` (page number) is renamed to `page` in `combine.py`.
- No financing, discount, or stock data is available from this source.
- Some listings are accessories or bundles (e.g. "Apple iPad case", "Magic Keyboard") — these are included in the raw CSV but should be filtered by price threshold (> 100 AZN) for device-only analysis.

---

## 8. w-t.az

**Script:** `scripts/wtaz.py`
**Output:** `data/wtaz.csv`

### Endpoint

```
GET https://www.w-t.az/k3+plansetler
```

### Pagination

**None** — the entire tablet catalogue (8 products) loads on a single SSR page. No pagination loop is needed.

### Product Card Selectors

| Field | Selector |
|-------|----------|
| Name | `.productName` |
| Product ID | `button.addToFavourite[data-id]` |
| Price | `.realPrice` — contains `959<sup>.00</sup>₼`; `get_text(separator="")` is used to join integer + decimal |
| Installment 6m | `label.month[data-price]` where label text matches `6 ay` |
| Installment 12m | `label.month[data-price]` where label text matches `12 ay` |
| Installment 18m | `label.month[data-price]` where label text matches `18 ay` |
| Active term | `label.month.checked` text → term |
| Active price | `label.month.checked[data-price]` |
| Campaign | `.cashCampaign p` text |
| URL | `a.productUrl[href]` |
| Image | `.productImage-img[src]` |

### Known Issues / Notes

- `price` is renamed to `price_current` in `combine.py`.
- `campaign` is renamed to `special_offer` in `combine.py`.
- The installment `data-price` attribute stores the monthly payment as a plain number string.

---

## 9. soliton.az

**Script:** `scripts/soliton.py`
**Output:** `data/soliton.csv`

### Endpoint

```
POST https://soliton.az/ajax-requests.php
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest
```

### Payload

```
action=loadProducts
sectionID=67          (tablets category)
brandID=0             (all brands)
offset=0              (increments by limit)
limit=15
sorting=              (empty)
```

### Response Schema

```json
{
  "html":       "<HTML fragment with .product-item cards>",
  "hasMore":    true,
  "totalCount": 53,
  "loadedCount": 15,
  "availableFilters": {...}
}
```

### Pagination

Offset-based. The first batch (`offset=0`) returns `totalCount`. All remaining offsets (`15, 30, 45, …`) are computed upfront and fetched **concurrently**.

### Product Card Selectors

| Field | Source |
|-------|--------|
| Name | `card[data-title]` attribute |
| Product ID | `span.icon.compare[data-item-id]` |
| Brand ID | `card[data-brandid]` attribute |
| Current price | `card[data-price]` attribute |
| Old price | `.prodPrice .creditPrice` text |
| Discount % | `.saleStar .percent` text |
| Discount amount | `.saleStar .moneydif .amount` text |
| Installment 6m | `.monthlyPayment[data-month="6"] .amount` |
| Installment 12m | `.monthlyPayment[data-month="12"] .amount` |
| Installment 18m | `.monthlyPayment[data-month="18"] .amount` |
| In stock | absence of `.outofstock` element → `"True"` / `"False"` |
| Special offer | `.specialOffers .offer .label` text |
| Category | `a.prodSection` text |
| URL | `a.prodTitle[href]` or `a.thumbHolder[href]` |
| Image | `.pic img[src]` |

### Known Issues / Notes

- **zstd encoding:** The server responds with `Content-Encoding: zstd` which `aiohttp` cannot decompress. The `Accept-Encoding` header is set to `"gzip, deflate, br"` (excluding `zstd`) for the aiohttp path. The curl_cffi fallback handles zstd natively.
- **UnicodeDecodeError:** Raw bytes are read with `resp.read()` and decoded as `raw.decode("utf-8", errors="replace")` instead of using `resp.json()` directly — this handles non-UTF-8 byte sequences in the response.
- `brand_id` is renamed to `brand` in `combine.py`.
- `offset` is renamed to `page` in `combine.py`.
- All 53 listings in the current dataset have `in_stock = "False"` — this correctly reflects the site's state at the time of collection (all items shown with the `.outofstock` class).

---

## 10. bytelecom.az

**Script:** `scripts/bytelecom.py`
**Output:** `data/bytelecom.csv`

### Endpoint

```
GET https://bytelecom.az/az/category/plansetler?page=N
```

### Pagination

Bootstrap pagination (`ul.pagination li button.page-link`). The highest numeric value in any page-link button is taken as the last page. Currently 2 pages (26 products total).

### Product Card Selectors

Cards are rendered as **Livewire components**. The raw product model data is embedded in the `wire:initial-data` attribute as a JSON string, but the visible HTML attributes are sufficient for extraction:

| Field | Source |
|-------|--------|
| Name | `a.product-name` text |
| Product ID | `wire:click="toggleWishlist(ID)"` — extracted with `re.search(r"toggleWishlist\((\d+)\)")` |
| Current price | `.prices h5.price` text (discounted price) |
| Old price | `.prices h6.discount-price` text (original price) |
| Badges | `.badge-item p` text elements |
| Is new | presence of `.new-product` element |
| URL | `a[href]` wrapping `.product-img` |
| Image | `.product-img img[src]` |

### Known Issues / Notes

- `badges` is renamed to `special_offer` in `combine.py`.
- Installment financing is **not available** on this platform — all `installment_*` columns are empty in `data.csv`.
- The `.prices h6.discount-price` element holds the **original (higher)** price, and `.prices h5.price` holds the **sale (lower)** price — note the naming is inverted relative to typical conventions.

---

## 11. texnohome.az

**Script:** `scripts/texnohome.py`
**Output:** `data/texnohome.csv`

### Endpoint

```
GET https://texnohome.az/smartfon-ve-plansetler/plansetler?page=N
```

### Pagination

`?page=N` links in `ul.pagination a[href]`. The highest `?page=N` value found is used as the last page. Currently 2 pages (21 products total).

### Product Card Selectors

| Field | Source |
|-------|--------|
| Name | `h4.title a` text |
| Product ID | `onclick="compare.add('ID', this);"` — extracted with `re.search(r"compare\.add\('(\d+)'")` |
| Current price | `.price span.price-new` text |
| Old price | `.price span.price-old` text |
| Discount % | `.product-label .square` text (e.g. `"-16%"`) |
| In stock | `.pw-label.stock` text contains `"yoxdur"` → `"False"` |
| Labels | `.pw-label` text elements (excluding stock label) |
| URL | `div.image a[href]` |
| Image | `div.image img[src]` |

### Known Issues / Notes

- `labels` is renamed to `special_offer` in `combine.py`.
- Out-of-stock products show `price_current = "0.00"`. These are excluded from price analysis by filtering prices ≤ 1.0 AZN.
- 12 of 21 products were out of stock at collection time, leaving only 9 with valid prices.
- The category listing URL (`/smartfon-ve-plansetler/plansetler`) was discovered by navigating from the product URL `/planset-honor-pad-8-6gb-128gb-blue` — the category path is not immediately obvious from product URLs alone.
