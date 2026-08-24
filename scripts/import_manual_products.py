#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把「PYP蝦皮市場手動蒐集表.xlsx」的「商品資料」工作表匯入成
data/raw/<日期>.jsonl，格式跟自動化爬蟲（scraper/shopee_scraper.py）輸出的
完全相容，scripts/build_reports.py 可以直接讀取、照樣彙整季報/年報，
不需要改任何東西。

使用方式
--------
    python scripts/import_manual_products.py 路徑/到/PYP蝦皮市場手動蒐集表.xlsx

若同一份 Excel 裡有好幾個不同日期的資料列，會依日期分別寫成對應的
data/raw/<日期>.jsonl（同一天的資料寫進同一個檔案；同一天內同一個商品
出現兩次以最後一列為準）。跑完之後記得接著執行：

    python scripts/build_reports.py

重新產生 docs/data/*.json，儀表板才會反映新資料。

商品 ID 是怎麼來的？
--------------------
為了讓同一個商品在「不同輪手動蒐集」之間可以被系統認出是同一個、才能算出
季度銷量差值，這支腳本會盡量從「商品連結」欄位解析出蝦皮網址裡真正的
shopid/itemid（支援 `/product/<shopid>/<itemid>` 和 `<商品名>-i.<shopid>.<itemid>`
兩種常見網址格式）。如果連結格式辨識不出來（例如貼的是短網址），會退而
求其次用「商品名稱＋賣家名稱」算一個穩定的代用 ID——這代表**之後每一輪
填寫，同一個商品的名稱、賣家名稱都要打得盡量一致**，系統才認得出是同一個
商品。腳本執行完會列出哪些商品用了這種備援機制，提醒你留意。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

import openpyxl

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
TAIPEI_TZ = timezone(timedelta(hours=8))

SHEET_NAME = "商品資料"
FIRST_DATA_ROW = 3  # 第1列是標題、第2列是範例，資料從第3列開始


def extract_ids(product_url: str, shop_url: str, item_name: str, shop_name: str):
    """回傳 (shopid, itemid, is_synthetic)。優先從網址解析真正的蝦皮 ID，
    解析不出來才用名稱算代用 ID（is_synthetic=True）。"""
    m = re.search(r"/product/(\d+)/(\d+)", product_url or "")
    if m:
        return int(m.group(1)), int(m.group(2)), False
    m = re.search(r"-i\.(\d+)\.(\d+)", product_url or "")
    if m:
        return int(m.group(1)), int(m.group(2)), False

    shopid = None
    m2 = re.search(r"/shop/(\d+)", shop_url or "")
    if m2:
        shopid = int(m2.group(1))

    key = f"{item_name.strip()}|{shop_name.strip()}"
    synthetic_itemid = -int(hashlib.md5(key.encode("utf-8")).hexdigest()[:10], 16)
    if shopid is None:
        shopid = -int(hashlib.md5(shop_name.strip().encode("utf-8")).hexdigest()[:8], 16)
    return shopid, synthetic_itemid, True


def to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/import_manual_products.py <xlsx 檔案路徑>")
        return 1
    xlsx_path = Path(sys.argv[1])
    if not xlsx_path.exists():
        print(f"找不到檔案：{xlsx_path}")
        return 1

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        print(f"這個活頁簿裡沒有「{SHEET_NAME}」工作表，請確認檔案是不是對的範本。")
        return 1
    ws = wb[SHEET_NAME]

    rows_by_date: dict[str, dict] = defaultdict(dict)
    synthetic_warnings = []
    now_iso = datetime.now(TAIPEI_TZ).isoformat()

    for r in range(FIRST_DATA_ROW, ws.max_row + 1):
        values = [ws.cell(row=r, column=c).value for c in range(1, 10)]
        (date_val, category, size, item_name, shop_name, product_url,
         price, sold, _note) = values

        if not item_name or not str(item_name).strip():
            continue  # 空白列，略過

        if date_val is None:
            date_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
        elif isinstance(date_val, datetime):
            date_str = date_val.strftime("%Y-%m-%d")
        else:
            date_str = str(date_val).strip()

        item_name_s = str(item_name).strip()
        shop_name_s = str(shop_name).strip() if shop_name else ""

        shopid, itemid, is_synthetic = extract_ids(
            str(product_url or ""), "", item_name_s, shop_name_s
        )
        if is_synthetic:
            synthetic_warnings.append(f"第 {r} 列「{item_name_s}」")

        row = {
            "date": date_str,
            "timestamp": now_iso,
            "keyword": "(手動輸入)",
            "itemid": itemid,
            "shopid": shopid,
            "shop_name": shop_name_s or None,
            "item_name": item_name_s,
            "category": str(category).strip() if category else None,
            "size": str(size).strip() if size else None,
            "price_twd": to_float(price),
            "sold_recent": None,
            "historical_sold": to_int(sold),
            "rating_avg": None,
            "rating_count": None,
            "stock": None,
            "url": str(product_url or ""),
            "shop_url": None,
        }
        rows_by_date[date_str][itemid] = row  # 同一天同一商品，用最後一列覆蓋

    if not rows_by_date:
        print("Excel 裡沒有任何有效的資料列（商品名稱是空的），沒有東西可以匯入。")
        return 1

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for date_str, items in rows_by_date.items():
        out_path = RAW_DATA_DIR / f"{date_str}.jsonl"
        merged = sorted(items.values(), key=lambda x: -(x["historical_sold"] or 0))
        with open(out_path, "w", encoding="utf-8") as f:
            for row in merged:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"寫入 {out_path}（{len(merged)} 筆商品）")

    if synthetic_warnings:
        print("\n⚠️  以下商品沒辦法從連結解析出蝦皮真正的商品 ID，改用「商品名稱＋賣家名稱」算代用 ID：")
        for w in synthetic_warnings:
            print("   -", w)
        print("下次填這些商品時，商品名稱、賣家名稱請盡量打得跟這次一模一樣，")
        print("系統才能認得出是同一個商品、正確算出銷量變化。建議盡量把「商品連結」欄位填完整，")
        print("避免要靠這個備援機制。")

    print("\n完成！接下來執行「python scripts/build_reports.py」重新產生季報/年報。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
