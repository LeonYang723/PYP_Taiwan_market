#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 data/raw/<日期>.jsonl 的每日快照，彙整成前端儀表板要用的季報 / 年報 JSON。

核心邏輯
--------
蝦皮商品頁上的「已售出 N 件」是**上架以來的累計銷量**（historical_sold）。
我們每天抓一次快照，等於在時間軸上取樣這條累計曲線。要估算「某一季賣了幾件」，
做法是：

    該季賣出估計值 = 季末前最後一次快照的累計銷量 - 季初前最後一次快照的累計銷量

如果我們在季初之前還沒開始追蹤這個商品（例如系統剛上線、或商品是季中才上架/才第一次
被搜尋到），就沒有「季初基準值」可用，這時退而求其次用「該季第一次快照」當基準，
估算值一定會偏低（因為漏掉了季初到我們第一次看到它之間賣掉的量），這種情況會標記
"partial": true，前端會用符號提醒使用者這個數字是低估值、不是精確值。

輸出檔案（寫到 docs/data/，給 GitHub Pages 前端讀取）
----------------------------------------------------
- meta.json       整體統計摘要（追蹤天數、商品數、最後更新時間等）
- latest.json     目前追蹤中的商品清單（最新一次快照）
- quarterly.json  依「年-季」彙整的銷量估計（商品層級 + 賣家層級 + 整體彙總）
- annual.json     依「年」彙整的銷量估計
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
OUT_DIR = REPO_ROOT / "docs" / "data"
TAIPEI_TZ = timezone(timedelta(hours=8))


def load_all_snapshots() -> list[dict]:
    """讀取所有每日快照檔，回傳攤平後的 list，每筆多帶一個 date 欄位方便排序。"""
    rows: list[dict] = []
    for path in sorted(RAW_DATA_DIR.glob("*.jsonl")):
        date_str = path.stem  # YYYY-MM-DD
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                row.setdefault("date", date_str)
                rows.append(row)
    return rows


def quarter_of(date: datetime) -> int:
    return (date.month - 1) // 3 + 1


def quarter_bounds(year: int, quarter: int) -> tuple[datetime, datetime]:
    start_month = (quarter - 1) * 3 + 1
    start = datetime(year, start_month, 1, tzinfo=TAIPEI_TZ)
    if quarter == 4:
        end = datetime(year + 1, 1, 1, tzinfo=TAIPEI_TZ)
    else:
        end = datetime(year, start_month + 3, 1, tzinfo=TAIPEI_TZ)
    return start, end


def build_item_timeseries(rows: list[dict]) -> dict[int, list[dict]]:
    """itemid -> 依日期排序的快照 list（每筆保留 date/historical_sold/price/name/shop 等）。"""
    by_item: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        itemid = r.get("itemid")
        if itemid is None:
            continue
        by_item[itemid].append(r)
    for itemid in by_item:
        by_item[itemid].sort(key=lambda x: x["date"])
    return by_item


def estimate_period_sold(
    snaps: list[dict], period_start: datetime, period_end: datetime
) -> tuple[int | None, bool]:
    """回傳 (估計銷量, 是否為低估值 partial)。若期間內完全沒有快照，回傳 (None, False)。"""

    def to_dt(d: str) -> datetime:
        return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=TAIPEI_TZ)

    before = [s for s in snaps if to_dt(s["date"]) < period_start]
    within = [s for s in snaps if period_start <= to_dt(s["date"]) < period_end]

    if not within:
        return None, False

    end_snap = within[-1]
    end_val = end_snap.get("historical_sold")
    if end_val is None:
        return None, False

    if before:
        base_val = before[-1].get("historical_sold")
        partial = False
    else:
        base_val = within[0].get("historical_sold")
        partial = len(within) > 1  # 只有一筆快照時無法算 delta，視為 0 且非 partial（單純沒資料）

    if base_val is None:
        return None, False

    estimate = end_val - base_val
    if estimate < 0:
        # historical_sold 理論上不會下降；若出現負值代表資料異常（例如商品被下架重新上架、
        # 或蝦皮那邊的欄位定義變動），保守起見回報 0 並標記為 partial 提醒需要人工檢查。
        estimate = 0
        partial = True

    return estimate, partial


def latest_snapshot_per_item(by_item: dict[int, list[dict]]) -> list[dict]:
    out = []
    for itemid, snaps in by_item.items():
        latest = snaps[-1]
        out.append(latest)
    out.sort(key=lambda x: -(x.get("historical_sold") or 0))
    return out


def build_period_report(
    by_item: dict[int, list[dict]], periods: list[tuple[str, datetime, datetime]]
) -> list[dict]:
    """periods: list of (label, start, end)。回傳每個 period 的彙整報表。"""
    report = []
    for label, start, end in periods:
        item_rows = []
        for itemid, snaps in by_item.items():
            est, partial = estimate_period_sold(snaps, start, end)
            if est is None:
                continue
            latest_in_period = [
                s
                for s in snaps
                if start <= datetime.strptime(s["date"], "%Y-%m-%d").replace(tzinfo=TAIPEI_TZ) < end
            ][-1]
            item_rows.append(
                {
                    "itemid": itemid,
                    "shopid": latest_in_period.get("shopid"),
                    "shop_name": latest_in_period.get("shop_name"),
                    "shop_url": latest_in_period.get("shop_url"),
                    "item_name": latest_in_period.get("item_name"),
                    "keyword": latest_in_period.get("keyword"),
                    "category": latest_in_period.get("category"),
                    "size": latest_in_period.get("size"),
                    "price_twd": latest_in_period.get("price_twd"),
                    "url": latest_in_period.get("url"),
                    "sold_estimate": est,
                    "partial": partial,
                }
            )

        item_rows.sort(key=lambda x: -x["sold_estimate"])

        shop_totals: dict[int, dict] = {}
        for row in item_rows:
            shopid = row["shopid"]
            if shopid not in shop_totals:
                shop_totals[shopid] = {
                    "shopid": shopid,
                    "shop_name": row.get("shop_name"),
                    "shop_url": row.get("shop_url"),
                    "sold_estimate": 0,
                    "item_count": 0,
                    "partial": False,
                }
            shop_totals[shopid]["sold_estimate"] += row["sold_estimate"]
            shop_totals[shopid]["item_count"] += 1
            shop_totals[shopid]["partial"] = shop_totals[shopid]["partial"] or row["partial"]
        shop_rows = sorted(shop_totals.values(), key=lambda x: -x["sold_estimate"])

        report.append(
            {
                "period": label,
                "total_sold_estimate": sum(r["sold_estimate"] for r in item_rows),
                "total_items": len(item_rows),
                "total_shops": len(shop_rows),
                "has_partial_data": any(r["partial"] for r in item_rows),
                "items": item_rows,
                "shops": shop_rows,
            }
        )
    return report


def main() -> int:
    rows = load_all_snapshots()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not rows:
        meta = {
            "last_updated": datetime.now(TAIPEI_TZ).isoformat(),
            "status": "no_data",
            "message": "尚未有任何快照資料，請先用 scripts/import_manual_products.py 匯入手動蒐集表的資料。",
            "days_tracked": 0,
        }
        (OUT_DIR / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        for name in ("latest.json", "quarterly.json", "annual.json"):
            (OUT_DIR / name).write_text("[]", encoding="utf-8")
        print("沒有任何快照資料可彙整。已輸出空白報表。")
        return 0

    by_item = build_item_timeseries(rows)
    all_dates = sorted({r["date"] for r in rows})

    # --- 季報 ---
    quarters_seen: set[tuple[int, int]] = set()
    for d in all_dates:
        dt = datetime.strptime(d, "%Y-%m-%d")
        quarters_seen.add((dt.year, quarter_of(dt)))
    quarter_periods = []
    for year, q in sorted(quarters_seen):
        start, end = quarter_bounds(year, q)
        quarter_periods.append((f"{year}-Q{q}", start, end))
    quarterly_report = build_period_report(by_item, quarter_periods)

    # --- 年報 ---
    years_seen = sorted({datetime.strptime(d, "%Y-%m-%d").year for d in all_dates})
    annual_periods = [
        (str(y), datetime(y, 1, 1, tzinfo=TAIPEI_TZ), datetime(y + 1, 1, 1, tzinfo=TAIPEI_TZ))
        for y in years_seen
    ]
    annual_report = build_period_report(by_item, annual_periods)

    # --- 最新商品清單 ---
    latest = latest_snapshot_per_item(by_item)

    meta = {
        "last_updated": datetime.now(TAIPEI_TZ).isoformat(),
        "status": "ok",
        "days_tracked": len(all_dates),
        "first_date": all_dates[0],
        "last_date": all_dates[-1],
        "total_items_ever_seen": len(by_item),
        "total_items_latest": len(latest),
    }

    (OUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "latest.json").write_text(
        json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "quarterly.json").write_text(
        json.dumps(quarterly_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT_DIR / "annual.json").write_text(
        json.dumps(annual_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"報表完成：追蹤 {len(all_dates)} 天、{len(by_item)} 個不重複商品，"
        f"輸出到 {OUT_DIR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
