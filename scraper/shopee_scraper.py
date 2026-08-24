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

**為什麼用真的瀏覽器，而不是直接發 HTTP 請求？（這支腳本走過的兩個階段）**
------------------------------------------------------------------------
第一版是用 Python `requests` 直接打 API，結果蝦皮在連線層級（TLS 指紋）就直接
判斷「這不是真的瀏覽器」，回傳 403，改 headers 完全沒用。

第二版改成 Playwright 開真的 Chromium，但做法是「載入首頁後，在頁面的 JS 環境裡
自己組一個 API 網址、呼叫 `fetch()`」——結果 403 是不見了（連線層級的偵測騙過去
了），但蝦皮的 API 改成回傳一個看起來正常的 JSON、但內容其實是風控系統的錯誤物件
（例如 `{"error": 90309999, ...}`，沒有真正的商品資料）。這代表蝦皮的防護不只看
連線特徵，還會看「這個 API 請求本身合不合理」——真正的搜尋頁在呼叫這個 API 之前，
會先執行一段前端的風控/裝置指紋 JS，把算出來的 token 或簽章塞進請求裡，我們自己
另外組的 `fetch()` 沒有這個 token，所以被判定為異常請求、拒絕。

現在這版（第三版）的做法：**不要自己組 API 網址呼叫，而是直接讓 Playwright 導航
到真正的搜尋頁面**（`https://shopee.tw/search?keyword=...`），讓蝦皮自己的前端
JS 完整跑一遍（包含它自己的風控/指紋邏輯），然後我們在旁邊「偷聽」瀏覽器自己發出
的那個搜尋 API 請求的回應內容。這樣送出去的請求，從蝦皮的角度看，跟真人打開瀏覽器
搜尋是完全一樣的行為，理論上能拿到真正的搜尋結果。

**這樣做仍然不保證 100% 不被擋**，如果蝦皮的風控還會看更進一步的行為模式（例如
滑鼠移動、頁面停留時間、多次搜尋之間的間隔規律性），現在這版還是可能被擋或拿到
空結果。如果又開始持續失敗，請參考 README「當爬蟲被封鎖時怎麼辦」的進階選項
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

SEARCH_PAGE_URL = "https://shopee.tw/search"
SEARCH_API_PATTERN = "/api/v4/search/search_items"
SHOP_DETAIL_ENDPOINT = "https://shopee.tw/api/v4/shop/get_shop_detail"
PAGE_SIZE = 60

# (僅供 enrich_shop_names 使用) 在頁面的 JS 環境裡執行 fetch，回傳 {status, body}。
# 商品搜尋已經改成用真正的頁面導航去攔截回應（見 fetch_search_page），
# 但賣家名稱查詢沒有對應的「真實頁面」可以導航，暫時還是用這種直接 fetch 的方式，
# 如果之後發現賣家名稱也一樣被擋（回傳風控錯誤物件），代表這個函式也需要比照
# 搜尋改成導航到賣場頁面再攔截回應。
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


def fetch_search_page(page: Page, keyword: str, page_index: int, retries: int = 3) -> dict | None:
    """導航到蝦皮真正的搜尋頁面（而不是自己組 API 網址呼叫），同時攔截頁面自己
    發出的搜尋 API 請求的回應。這樣送出去的請求會帶有蝦皮前端 JS 自己算好的
    風控/裝置指紋 token，比起我們自己組網址呼叫 fetch 更接近真人操作。"""
    search_url = f"{SEARCH_PAGE_URL}?{urlencode({'keyword': keyword, 'page': page_index})}"

    for attempt in range(1, retries + 1):
        try:
            with page.expect_response(
                lambda r: SEARCH_API_PATTERN in r.url, timeout=20000
            ) as response_info:
                page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            response = response_info.value
            status = response.status
            try:
                body = response.json()
            except Exception:
                body = None
        except PlaywrightTimeoutError:
            log.warning(
                "[%s] 導航到搜尋頁後，等不到搜尋 API 的回應（第 %d/%d 次嘗試），"
                "可能是頁面被導去驗證頁、或載入太慢",
                keyword,
                attempt,
                retries,
            )
            time.sleep(3 * attempt)
            continue
        except Exception as e:
            log.warning(
                "[%s] 導航或攔截搜尋 API 回應失敗（第 %d/%d 次嘗試）：%s",
                keyword,
                attempt,
                retries,
                e,
            )
            time.sleep(2 * attempt)
            continue

        if status == 200 and body is not None:
            return body

        if status in (403, 429):
            log.warning(
                "[%s] 搜尋 API 回應 %d，疑似被反爬蟲機制擋下（第 %d/%d 次嘗試），等待後重試",
                keyword,
                status,
                attempt,
                retries,
            )
            time.sleep(5 * attempt)
            continue

        log.warning(
            "[%s] 非預期的回應（狀態碼=%s，第 %d/%d 次嘗試）",
            keyword,
            status,
            attempt,
            retries,
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
        log.info("抓取關鍵字 %r 第 %d 頁", keyword, p + 1)
        payload = fetch_search_page(page, keyword, p)
        if payload is None:
            break

        items = parse_items(payload, keyword, now)
        if not items:
            log.info("[%s] 第 %d 頁沒有更多商品，停止翻頁", keyword, p + 1)
            if p == 0:
                # 除錯用：第一頁就是空的，把回應內容的重點資訊印出來，方便判斷
                # 到底是「真的沒有符合的商品」、「回應格式跟預期不一樣」、還是
                # 「被導去了一個看起來像 JSON、內容其實是驗證/攔截頁」。
                raw_snippet = json.dumps(payload, ensure_ascii=False)[:800]
                log.info(
                    "[%s] 除錯資訊：payload 頂層欄位=%s，total_count=%s，"
                    "error 欄位=%s，內容前 800 字=%s",
                    keyword,
                    list(payload.keys()),
                    payload.get("total_count"),
                    payload.get("error") or payload.get("error_msg"),
                    raw_snippet,
                )
            break
        results.extend(items)

        if len(items) < PAGE_SIZE:
            # 這一頁回來的數量沒有滿一頁，代表已經是最後一頁了
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
