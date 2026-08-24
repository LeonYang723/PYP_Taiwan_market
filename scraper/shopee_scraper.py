#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮「PYP」品牌市場監控爬蟲
=================================

用途
----
呼叫蝦皮台灣站（shopee.tw）**未公開、非官方**的內部搜尋 API（前端網頁本身也是呼叫這個
API 來顯示搜尋結果），依 config/keywords.yaml 設定的關鍵字清單，抓取蝦皮全站上符合
關鍵字的商品資料（商品名稱、價格、已售出數量、賣家 shopid 等），存成每日快照，
供 scripts/build_reports.py 進一步彙整成季報/年報。

**重要風險提醒（請務必先讀 README.md 的「風險與限制」章節）**
----------------------------------------------------------------
1. 這不是蝦皮官方提供的 API，是逆向工程網頁前端行為得到的內部端點。蝦皮隨時可能改變
   回傳格式、加上更嚴格的驗證（例如需要特定 cookie / token / 圖形驗證碼），導致這支
   腳本失效，需要更新。
2. 這種抓取方式很可能違反蝦皮的服務條款。是否要以此方式做市場監控，是一個你們公司
   要自行評估的商業/法遵決定，不是單純的技術問題。
3. GitHub Actions 執行環境的出口 IP 屬於雲端機房（Azure/GitHub），比一般家用/公司網路
   更容易被平台的反爬蟲機制標記或封鎖。腳本內建了隨機延遲、正常瀏覽器 headers、
   逐步 backoff 重試，降低被擋機率，但無法保證長期穩定可用。若持續被擋，請參考
   README 的「當爬蟲被封鎖時怎麼辦」。

輸出
----
data/raw/<YYYY-MM-DD>.jsonl
    每行一筆 JSON，代表當天抓到的一個商品快照。同一天重複執行會覆蓋當天檔案
    （視為「當天最新快照」，不是每次執行都疊加）。
"""

from __future__ import annotations

import json
import logging
import random
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "keywords.yaml"
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"

TAIPEI_TZ = timezone(timedelta(hours=8))

# 模擬一般瀏覽器的 headers，降低被當成機器人流量的機率。
# User-Agent 建議定期更新成當下常見的瀏覽器版本。
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Referer": "https://shopee.tw/",
    "x-api-source": "pc",
}

SEARCH_ENDPOINT = "https://shopee.tw/api/v4/search/search_items"
PAGE_SIZE = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("shopee_scraper")


@dataclass
class ProductSnapshot:
    date: str
    timestamp: str
    keyword: str
    itemid: int
    shopid: int
    shop_name: str | None
    item_name: str
    price_twd: float | None
    sold_recent: int | None
    historical_sold: int | None
    rating_avg: float | None
    rating_count: int | None
    stock: int | None
    url: str
    shop_url: str


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


def new_session() -> requests.Session:
    """建立一個 session，先訪問首頁取得必要的 cookie，再拿去打搜尋 API。"""
    session = requests.Session()
    session.headers.update(BASE_HEADERS)
    try:
        session.get("https://shopee.tw/", timeout=15)
    except requests.RequestException as e:
        log.warning("預先訪問 shopee.tw 首頁失敗（可能不影響後續搜尋）：%s", e)
    return session


def fetch_search_page(
    session: requests.Session, keyword: str, offset: int, retries: int = 3
) -> dict | None:
    params = {
        "by": "relevancy",
        "keyword": keyword,
        "limit": PAGE_SIZE,
        "newest": offset,
        "order": "desc",
        "page_type": "search",
        "scenario": "PAGE_GLOBAL_SEARCH",
        "version": 2,
    }
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(SEARCH_ENDPOINT, params=params, timeout=20)
        except requests.RequestException as e:
            log.warning(
                "[%s] 請求失敗（第 %d/%d 次嘗試）：%s", keyword, attempt, retries, e
            )
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                log.warning("[%s] 回傳非 JSON，可能觸發了驗證頁面/封鎖頁", keyword)
                return None

        if resp.status_code in (403, 429):
            log.warning(
                "[%s] 收到 %d，疑似被反爬蟲機制擋下（第 %d/%d 次嘗試），等待後重試",
                keyword,
                resp.status_code,
                attempt,
                retries,
            )
            time.sleep(5 * attempt)
            continue

        log.warning("[%s] 非預期的 HTTP 狀態碼：%d", keyword, resp.status_code)
        time.sleep(2 * attempt)

    log.error("[%s] 已重試 %d 次仍失敗，放棄此頁", keyword, retries)
    return None


def parse_items(payload: dict, keyword: str, now: datetime) -> list[ProductSnapshot]:
    items = payload.get("items") or []
    out: list[ProductSnapshot] = []
    for entry in items:
        basic = entry.get("item_basic") or entry.get("item") or entry
        if not basic:
            continue
        try:
            itemid = int(basic.get("itemid"))
            shopid = int(basic.get("shopid"))
        except (TypeError, ValueError):
            continue

        raw_price = basic.get("price")
        price_twd = None
        if isinstance(raw_price, (int, float)) and raw_price > 0:
            # 蝦皮 API 的價格單位是「元 * 100000」
            price_twd = round(raw_price / 100000, 2)

        rating = basic.get("item_rating") or {}
        rating_star = rating.get("rating_star")
        rating_count = None
        if isinstance(rating.get("rating_count"), list) and rating["rating_count"]:
            rating_count = rating["rating_count"][0]

        snap = ProductSnapshot(
            date=now.strftime("%Y-%m-%d"),
            timestamp=now.isoformat(),
            keyword=keyword,
            itemid=itemid,
            shopid=shopid,
            shop_name=None,  # 預設不查詢賣家名稱，見 enrich_shop_names()
            item_name=basic.get("name", ""),
            price_twd=price_twd,
            sold_recent=basic.get("sold"),
            historical_sold=basic.get("historical_sold"),
            rating_avg=rating_star,
            rating_count=rating_count,
            stock=basic.get("stock"),
            url=f"https://shopee.tw/product/{shopid}/{itemid}",
            shop_url=f"https://shopee.tw/shop/{shopid}",
        )
        out.append(snap)
    return out


def scrape_keyword(
    session: requests.Session, keyword: str, max_pages: int, delay_range: tuple[int, int]
) -> list[ProductSnapshot]:
    now = datetime.now(TAIPEI_TZ)
    results: list[ProductSnapshot] = []
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        log.info("抓取關鍵字 %r 第 %d 頁 (offset=%d)", keyword, page + 1, offset)
        payload = fetch_search_page(session, keyword, offset)
        if payload is None:
            break

        items = parse_items(payload, keyword, now)
        if not items:
            log.info("[%s] 第 %d 頁沒有更多商品，停止翻頁", keyword, page + 1)
            break
        results.extend(items)

        total_count = payload.get("total_count")
        if total_count is not None and offset + PAGE_SIZE >= total_count:
            break

        time.sleep(random.uniform(*delay_range))
    return results


def enrich_shop_names(
    session: requests.Session, snapshots: list[ProductSnapshot], delay_range: tuple[int, int]
) -> None:
    """(選用) 針對抓到的每個不重複 shopid 額外呼叫一次蝦皮 API 取得賣家名稱。

    這一步預設是關閉的（見 config/keywords.yaml 的 enrich_shop_names），因為它會讓
    對蝦皮發出的請求數量大幅增加（每個不重複賣家都要多打一次 API），提高被封鎖的
    機率。如果你比較在意「知道是哪個賣家在賣」，可以在 config 打開它；如果比較在意
    「盡量降低被封鎖風險、求長期穩定運作」，建議保持關閉，改用商品清單裡的
    shop_url 手動點進去看賣家名稱即可。
    """
    unique_shopids = sorted({s.shopid for s in snapshots})
    log.info("開始查詢 %d 個不重複賣家的名稱...", len(unique_shopids))
    name_cache: dict[int, str | None] = {}

    for shopid in unique_shopids:
        try:
            resp = session.get(
                "https://shopee.tw/api/v4/shop/get_shop_detail",
                params={"shopid": shopid},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                name_cache[shopid] = data.get("name") or data.get("account", {}).get("username")
            else:
                name_cache[shopid] = None
        except (requests.RequestException, ValueError):
            name_cache[shopid] = None
        time.sleep(random.uniform(*delay_range))

    for s in snapshots:
        s.shop_name = name_cache.get(s.shopid)


def dedupe_by_itemid(snapshots: Iterable[ProductSnapshot]) -> list[ProductSnapshot]:
    """同一商品可能同時符合多個關鍵字；輸出時保留每個 itemid 第一次出現的關鍵字，
    但把符合的關鍵字都記錄下來（用逗號合併），方便後續分析。"""
    by_id: dict[int, ProductSnapshot] = {}
    keywords_by_id: dict[int, list[str]] = {}
    for snap in snapshots:
        keywords_by_id.setdefault(snap.itemid, [])
        if snap.keyword not in keywords_by_id[snap.itemid]:
            keywords_by_id[snap.itemid].append(snap.keyword)
        if snap.itemid not in by_id:
            by_id[snap.itemid] = snap

    merged = []
    for itemid, snap in by_id.items():
        merged_snap = asdict(snap)
        merged_snap["keyword"] = ",".join(keywords_by_id[itemid])
        merged.append(merged_snap)
    return merged


def main() -> int:
    cfg = load_config()
    keywords = cfg.get("keywords", [])
    max_pages = int(cfg.get("max_pages_per_keyword", 3))
    delay_range = tuple(cfg.get("request_delay_seconds", [3, 7]))

    if not keywords:
        log.error("config/keywords.yaml 沒有設定任何關鍵字，中止。")
        return 1

    session = new_session()
    all_snapshots: list[ProductSnapshot] = []
    any_success = False

    for kw in keywords:
        try:
            snaps = scrape_keyword(session, kw, max_pages, delay_range)
        except Exception:
            log.exception("關鍵字 %r 抓取過程發生未預期例外，跳過", kw)
            continue
        if snaps:
            any_success = True
        log.info("關鍵字 %r 抓到 %d 筆商品", kw, len(snaps))
        all_snapshots.extend(snaps)
        time.sleep(random.uniform(*delay_range))

    if not any_success:
        log.error(
            "所有關鍵字都沒有抓到任何資料，很可能已被蝦皮封鎖/擋下。"
            "不會寫入今天的快照檔，避免用空資料覆蓋掉先前的紀錄。"
            "請參考 README「當爬蟲被封鎖時怎麼辦」。"
        )
        return 2

    if cfg.get("enrich_shop_names", False):
        try:
            enrich_shop_names(session, all_snapshots, delay_range)
        except Exception:
            log.exception("查詢賣家名稱時發生例外，略過此步驟（不影響商品資料本身）")

    merged = dedupe_by_itemid(all_snapshots)
    merged.sort(key=lambda x: (x["keyword"], -(x["historical_sold"] or 0)))

    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    out_path = RAW_DATA_DIR / f"{today}.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for row in merged:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    log.info("完成！共 %d 筆不重複商品，寫入 %s", len(merged), out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
