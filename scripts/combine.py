"""
Combine all per-source CSVs into data/data.csv.

Normalisations applied:
  title          → name            (tapaz)
  code           → product_id      (irshad)
  price          → price_current   (tapaz, wtaz)
  availability   → in_stock        (irshad)
  brand_id       → brand           (soliton)
  product_type   → category        (irshad)
  campaign       → special_offer   (bakuelectronics, wtaz)
  promo_labels   → special_offer   (smartelectronics)
  labels         → special_offer   (texnohome)
  badges         → special_offer   (bytelecom)
  offset         → page            (soliton)
  batch          → page            (tapaz)

A `source` column (domain name) is prepended to every row.
"""

import csv
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# ── output column order ───────────────────────────────────────────────────────
OUTPUT_FIELDS = [
    "source",
    "name",
    "product_id",
    "sku",
    "brand",
    "category",
    "price_current",
    "price_old",
    "discount_pct",
    "discount_amount",
    "installment_6m",
    "installment_12m",
    "installment_18m",
    "installment_monthly",
    "installment_term",
    "installment",
    "installment_active_term",
    "installment_active_price",
    "in_stock",
    "is_new",
    "is_online",
    "quantity",
    "review_count",
    "rating",
    "special_offer",
    "region",
    "updated_at",
    "status",
    "kinds",
    "shop_id",
    "url",
    "image_url",
    "page",
]

# ── per-source configs ────────────────────────────────────────────────────────
# Each entry: (csv_filename, source_label, column_renames)
# column_renames: {original_col: output_col}  — only non-identity mappings needed
SOURCES = [
    ("bakuelectronics.csv", "bakuelectronics.az", {
        "campaign": "special_offer",
    }),
    ("birmarket.csv",       "birmarket.az",       {}),
    ("bytelecom.csv",       "bytelecom.az",       {
        "badges": "special_offer",
    }),
    ("irshad.csv",          "irshad.az",          {
        "code":         "product_id",
        "availability": "in_stock",
        "product_type": "category",
    }),
    ("kontakt.csv",         "kontakt.az",         {}),
    ("mgstore.csv",         "mgstore.az",         {}),
    ("smartelectronics.csv","smartelectronics.az",{
        "promo_labels": "special_offer",
    }),
    ("soliton.csv",         "soliton.az",         {
        "brand_id": "brand",
        "offset":   "page",
    }),
    ("tapaz.csv",           "tap.az",             {
        "title": "name",
        "price": "price_current",
        "batch": "page",
    }),
    ("texnohome.csv",       "texnohome.az",       {
        "labels": "special_offer",
    }),
    ("wtaz.csv",            "w-t.az",             {
        "price":    "price_current",
        "campaign": "special_offer",
    }),
]


def load_source(filename: str, source_label: str, renames: dict) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        print(f"  [skip] {filename} not found")
        return []

    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            out = {col: "" for col in OUTPUT_FIELDS}
            out["source"] = source_label

            for src_col, value in raw.items():
                dest_col = renames.get(src_col, src_col)
                if dest_col in OUTPUT_FIELDS:
                    out[dest_col] = value
                # silently drop columns not in OUTPUT_FIELDS

            rows.append(out)

    print(f"  {filename:30s}  {len(rows):4d} rows  → source={source_label}")
    return rows


def main() -> None:
    all_rows: list[dict] = []

    for filename, source_label, renames in SOURCES:
        all_rows.extend(load_source(filename, source_label, renames))

    output_path = DATA_DIR / "data.csv"
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\nCombined {len(all_rows)} rows → {output_path}")


if __name__ == "__main__":
    main()
