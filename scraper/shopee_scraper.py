#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
蝦皮「PYP」品牌市場監控爬蟲
=================================

用途
----
用真的瀏覽器引擎（Playwright + headless Chromium）打開蝦皮台灣站，在瀏覽器的 JS
環境裡呼叫蝦皮前端網頁自己在用的**未公開、非官方**內部搜尋 API，依
config/keywords.yaml 設定的關鍵字清單，抓取蝦皮全站上符合關鍵字的商品資料
（商品名稱、價格、已售出數量、賣家 shopid 等），存成每日快照，供
scripts/build_reports.py 進一步彙整成季報/年報。

**為什麼用真的瀏覽器，而不是直接發 HTTP 請求？**
------------------------------------------------
一開始這支腳本是用 Python `requests` 直接打 API，結果蝦皮在連線層級（TLS
指紋）就直接判斷「這不是真的瀏覽器」，回傳 403，就算把 User-Agent、Accept-Language
等 headers 改得再像瀏覽器也沒用——因為 `requests` 底層的 TLS handshake 特徵，
跟真的 Chrome/Firefox 就是不一樣，這是比 headers 更底層的偵測方式。
Playwright 開的是真的 Chromium，連線特徵就是真瀏覽器的特徵，所以改用它在頁面
的 JS 環境裡呼叫 `fetch()`，效果等同於「使用者真的打開瀏覽器、瀏覽器自己呼叫了
這個 API」，比較不容易被這種連線層級的防護擋下來。

**這樣做仍然不保證 100% 不被擋**，蝦皮還是可能用其他方式偵測（例如偵測
`navigator.webdriver`、瀏覽器指紋、請求頻率異常等），腳本裡已經做了幾個常見的
基礎規避（隱藏 `navigator.webdriver`、隨機延遲），但如果之後又開始持續失敗，
代表蝦皮的防護又升級了，請參考 README「當爬蟲被封鎖時怎麼辦」的進階選項
（例如換用有代理伺服器/住宅 IP 的方案，或退回人工蒐集）。

**重要風險提醒（請務必先讀 README.md 的「風險與限制」章節）**
----------------------------------------------------------------
1. 這不是蝦皮官方提供的 API，是逆向工程網頁前端行為得到的內部端點。蝦皮隨時可能改變
   回傳格式、加上更嚴格的驗證，導致這支腳本失效，需要更新。
2. 這種抓取方式很可能違反蝦皮的服務條款。是否要以此方式做市場監控，是一個你們公司
   要自行評估的商業/法遵決定，不是單純的技術問題。
3. 因為改用真的瀏覽器引擎，這支腳本比純 HTTP 請求版本慢很多、也吃更多資源
   （GitHub Actions 執行時間會從原本的 1-3 分鐘拉長到 5-10 分鐘左右屬正常）。

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
from typing import Iterable
from urllib.parse import urlencode

import yaml
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "keywords.yaml"
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"

TAIPEI_TZ = timezone(timedelta(hours=8))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

SEARCH_ENDPOINT = "https://shopee.tw/api/v4/search/search_items"
SHOP_DETAIL_ENDPOINT = "https://shopee.tw/api/v4/shop/get_shop_detail"
PAGE_SIZE = 60

# 在頁面的 JS 環境裡執行 fetch，回傳 {status, body}。
# 用瀏覽器自己的 fetch，是為了讓這個請求帶有真瀏覽器的連線/TLS 特徵，而不是
# Python requests 那種容易被連線層級防護辨識出來的特徵。
_FETCH_JS = """
async (url) => {
    try {
        const res = await fetch(url, {
            headers: { "Accept": "application/json" },
            credentials: "include",
        });
        let body = null;
        try { body = await res.json(); } catch (e) { body = null; }
        return { status: res.status, body };
    } catch (e) {
        return { status: 0, body: null, error: String(e) };
    }
}
"""

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


def new_page(playwright) -> tuple:
    """啟動 headless Chromium，開一個分頁，先訪問首頁讓蝦皮的前端 JS 有機會設好
    必要的 cookie，回傳 (browser, page) 讓呼叫端負責之後關閉 browser。"""
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=USER_AGENT,
        locale="zh-TW",
        viewport={"width": 1366, "height": 768},
    )
    # 隱藏最基本的「這是自動化工具」訊號，降低被簡單指紋偵測抓到的機率。
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    page = context.new_page()
    try:
        page.goto("https://shopee.tw/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeoutError:
        log.warning("預先訪問 shopee.tw 首頁逾時（可能不影響後續搜尋）")
    except Exception as e:
        # 不論是連線失敗、被導去驗證頁、還是其他問題，都不要讓整支腳本直接崩潰——
        # 讓它繼續往下嘗試呼叫搜尋 API，之後的重試/錯誤處理邏輯自然會處理失敗的情況。
        log.warning("預先訪問 shopee.tw 首頁時發生例外（可能不影響後續搜尋）：%s", e)
    return browser, page


def fetch_search_page(page: Page, keyword: str, offset: int, retries: int = 3) -> dict | None:
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
    url = f"{SEARCH_ENDPOINT}?{urlencode(params)}"

    for attempt in range(1, retries + 1):
        try:
            result = page.evaluate(_FETCH_JS, url)
        except Exception as e:
            log.warning(
                "[%s] 呼叫 fetch 失敗（第 %d/%d 次嘗試）：%s", keyword, attempt, retries, e
            )
            time.sleep(2 * attempt)
            continue

        status = result.get("status")
        body = result.get("body")

        if status == 200 and body is not None:
            return body

        if status in (403, 429):
            log.warning(
                "[%s] 收到 %d，疑似被反爬蟲機制擋下（第 %d/%d 次嘗試），等待後重試",
                keyword,
                status,
                attempt,
                retries,
            )
            time.sleep(5 * attempt)
            continue

        log.warning(
            "[%s] 非預期的回應（狀態碼=%s，第 %d/%d 次嘗試）：%s",
            keyword,
            status,
            attempt,
            retries,
            result.get("error", ""),
        )
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
    page: Page, keyword: str, max_pages: int, delay_range: tuple[int, int]
) -> list[ProductSnapshot]:
    now = datetime.now(TAIPEI_TZ)
    results: list[ProductSnapshot] = []
    for p in range(max_pages):
        offset = p * PAGE_SIZE
        log.info("抓取關鍵字 %r 第 %d 頁 (offset=%d)", keyword, p + 1, offset)
        payload = fetch_search_page(page, keyword, offset)
        if payload is None:
            break

        items = parse_items(payload, keyword, now)
        if not items:
            log.info("[%s] 第 %d 頁沒有更多商品，停止翻頁", keyword, p + 1)
            break
        results.extend(items)

        total_count = payload.get("total_count")
        if total_count is not None and offset + PAGE_SIZE >= total_count:
            break

        time.sleep(random.uniform(*delay_range))
    return results


def enrich_shop_names(
    page: Page, snapshots: list[ProductSnapshot], delay_range: tuple[int, int]
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
        url = f"{SHOP_DETAIL_ENDPOINT}?{urlencode({'shopid': shopid})}"
        try:
            result = page.evaluate(_FETCH_JS, url)
            if result.get("status") == 200 and result.get("body"):
                data = result["body"].get("data", {})
                name_cache[shopid] = data.get("name") or data.get("account", {}).get("username")
            else:
                name_cache[shopid] = None
        except Exception:
            name_cache[shopid] = None
        time.sleep(random.uniform(*delay_range))

    for s in snapshots:
        s.shop_name = name_cache.get(s.shopid)


def dedupe_by_itemid(snapshots: Iterable[ProductSnapshot]) -> list[dict]:
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

    all_snapshots: list[ProductSnapshot] = []
    any_success = False

    with sync_playwright() as playwright:
        browser, page = new_page(playwright)
        try:
            for kw in keywords:
                try:
                    snaps = scrape_keyword(page, kw, max_pages, delay_range)
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
                    enrich_shop_names(page, all_snapshots, delay_range)
                except Exception:
                    log.exception("查詢賣家名稱時發生例外，略過此步驟（不影響商品資料本身）")
        finally:
            browser.close()

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
